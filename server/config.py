import logging
import os
from typing import Optional, Tuple

import torch
from diarize_worker import LATENCY_PRESETS
from transcriber import EndpointConfig, NemotronASR, SubprocessDiarizer

try:
    from dotenv import load_dotenv

    # Load .env from the repo root (one level up from server/) if present.
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

log = logging.getLogger(__name__)
from model_management import ModelManager, ModelRegistry, ModelType, ModelConfig


def _setup_hf_token() -> None:
    """Ensure the HF_TOKEN env var is set for huggingface_hub.

    Reads from HUGGING_FACE_HUB_TOKEN (set in .env) or the ambient
    HF_AUTH_TOKEN and exports it as HF_TOKEN, which is the standard env var
    that huggingface_hub and NeMo's from_pretrained() read automatically.
    """
    token = os.environ.get("HUGGING_FACE_HUB_TOKEN") or os.environ.get("HF_AUTH_TOKEN")
    if token:
        os.environ["HF_TOKEN"] = token
        log.info("HF_TOKEN set from environment for HuggingFace model access.")
    else:
        log.warning(
            "No HuggingFace token configured (HUGGING_FACE_HUB_TOKEN / HF_AUTH_TOKEN). "
            "Model downloads are anonymous (lower rate limits)."
        )


# Default models, each pinned to a Hub commit. NeMo's from_pretrained serves
# whatever .nemo is already cached and never checks for a newer one, so the
# loaders download a named commit; upgrading the weights means changing it.
DEFAULT_ASR_MODEL = "nvidia/nemotron-speech-streaming-en-0.6b"
DEFAULT_ASR_REVISION = "ebe59e5a817142986528bbbee5dba8db7b38ed50"  # March 2026 retrain
DEFAULT_DIAR_MODEL = "nvidia/diar_streaming_sortformer_4spk-v2.1"
DEFAULT_DIAR_REVISION = "cd03eee90fbec18297ac31b8c21546e596b7f71c"


def _model_from_env(
    name_var: str, revision_var: str, default_name: str, default_revision: str
) -> Tuple[str, Optional[str]]:
    """(model, revision) from .env. The default pin only applies to the default
    model; another model without a revision loads that repo's main."""
    name = os.environ.get(name_var) or default_name
    revision = os.environ.get(revision_var) or (
        default_revision if name == default_name else None
    )
    return name, revision


def nemotron_asr_factory(model_id: str, config: ModelConfig) -> NemotronASR:
    """Factory function for creating NemotronASR instances."""
    return NemotronASR(model_id, config)


def subprocess_diarizer_factory(
    model_id: str, config: ModelConfig
) -> SubprocessDiarizer:
    """Factory function for creating SubprocessDiarizer instances."""
    return SubprocessDiarizer(model_id, config)


class AppConfig:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppConfig, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Device configuration
        self.device = torch.device("cuda")

        # Set HF_TOKEN env var once at startup so all NeMo / huggingface_hub
        # calls pick it up automatically (NeMo 2.6.x from_pretrained doesn't
        # accept hf_token as a keyword argument).
        _setup_hf_token()

        self.sample_rate = 16000

        # Models
        self.model_name, self.model_revision = _model_from_env(
            "MIMIKIKU_ASR_MODEL", "MIMIKIKU_ASR_REVISION",
            DEFAULT_ASR_MODEL, DEFAULT_ASR_REVISION,
        )
        self.diarization_model_name, self.diarization_model_revision = _model_from_env(
            "MIMIKIKU_DIAR_MODEL", "MIMIKIKU_DIAR_REVISION",
            DEFAULT_DIAR_MODEL, DEFAULT_DIAR_REVISION,
        )

        self.model_ttl = 300  # 5 minutes

        # Streaming parameters. Nemotron's attention context [left, right]
        # sets its chunk size: [70,13]=1.12s (most accurate), [70,6]=0.56s,
        # [70,1]=0.16s, [70,0]=0.08s.
        self.asr_att_context_size = [70, 13]
        # Sortformer latency preset (see diarize_worker.LATENCY_PRESETS).
        self.diar_latency_preset = "low"
        self.endpoint = EndpointConfig(diar_wait_frames=self._diar_wait_frames())

        # How much client audio (2048-sample chunks, 128 ms each) is grouped
        # before running the models. The ASR only steps when a full chunk is
        # buffered, so this mainly sets how often we poll.
        samples_per_chunk = 2048
        self.batch_size = max(1, round(0.5 * self.sample_rate / samples_per_chunk))

        # Initialize model management system
        self._setup_model_management()

    def _diar_wait_frames(self) -> int:
        """Wait for diarization of an utterance's last word before committing."""
        preset = LATENCY_PRESETS[self.diar_latency_preset]
        return preset["chunk_len"] + preset["chunk_right_context"] + 25

    # service_settings "asr_chunk" -> Nemotron attention context
    ASR_CHUNKS = {"1.12": [70, 13], "0.56": [70, 6], "0.16": [70, 1]}

    def apply_restart_settings(self, values: dict) -> None:
        """Apply the admin's model-level settings. Call at startup, before any model loads:
        they're baked into the models (and the model TTL is part of the model id)."""
        self.asr_att_context_size = self.ASR_CHUNKS[values["asr_chunk"]]
        self.diar_latency_preset = values["diar_preset"]
        self.model_ttl = int(values["asr_idle_ttl_min"]) * 60
        self.endpoint.diar_wait_frames = self._diar_wait_frames()
        for config in self._asr_configs:
            config.att_context_size = self.asr_att_context_size
            config.ttl_seconds = self.model_ttl
        self.diarization_model_config.diar_latency_preset = self.diar_latency_preset
        log.info(
            f"Service settings: ASR context {self.asr_att_context_size}, diarizer preset "
            f"{self.diar_latency_preset}, ASR idle TTL {self.model_ttl}s"
        )

    def recording_endpoint(self, rec) -> EndpointConfig:
        """A recording's endpoint settings: the admin's per-recording values (a
        service_settings.RecordingSettings) plus the preset-derived diarization wait."""
        return EndpointConfig(
            pause_frames=rec.pause_frames,
            max_utterance_frames=rec.max_utterance_frames,
            diar_wait_frames=self.endpoint.diar_wait_frames,
            min_activity=rec.min_activity,
            min_run_frames=rec.min_run_frames,
        )

    def _setup_model_management(self):
        """Initialize the model management system."""
        self.model_registry = ModelRegistry()

        # Register model types
        # Note: SubprocessDiarizer is created directly, not through model manager
        self.model_registry.register_model_type(
            ModelType.NEMOTRON_ASR, nemotron_asr_factory
        )

        self.model_manager = ModelManager(self.model_registry)

        # 1. Setup Diarization Config
        diar_fallbacks = []

        self.diarization_model_config = ModelConfig(
            model_type=ModelType.SORTFORMER_DIARIZATION,
            model_name=self.diarization_model_name,
            device="cuda",  # Run on GPU (in subprocess)
            ttl_seconds=self.model_ttl,
            max_memory_mb=2048,  # Used as estimate for resource monitoring
            fallback_configs=diar_fallbacks,
            lazy_loading=True,
            priority=1,
        )
        self.diarization_model_config.revision = self.diarization_model_revision
        self.diarization_model_config.diar_latency_preset = self.diar_latency_preset
        # HF_TOKEN is set globally via the environment; no need to attach it
        # to individual ModelConfig instances.

        self.model_registry.register_fallback_configs(
            ModelType.SORTFORMER_DIARIZATION, diar_fallbacks
        )

        # 2. Setup ASR Config
        asr_fallbacks = []
        if str(self.device) != "cpu":
            asr_fallbacks.append(
                self._asr_config(device="cpu", max_memory_mb=2048, priority=5)
            )
        self.nemotron_model_config = self._asr_config(
            device="cuda",
            max_memory_mb=4096,  # 0.6B model + overhead
            priority=10,
            fallback_configs=asr_fallbacks,
        )
        self.model_registry.register_fallback_configs(
            ModelType.NEMOTRON_ASR, asr_fallbacks
        )
        self._asr_configs = [self.nemotron_model_config, *asr_fallbacks]

    def _asr_config(self, device: str, **kwargs) -> ModelConfig:
        config = ModelConfig(
            model_type=ModelType.NEMOTRON_ASR,
            model_name=self.model_name,
            device=device,
            ttl_seconds=self.model_ttl,
            lazy_loading=True,
            **kwargs,
        )
        config.att_context_size = self.asr_att_context_size
        config.revision = self.model_revision
        config.endpoint = self.endpoint
        return config

    async def start_model_manager(self):
        await self.model_manager.start()

    async def stop_model_manager(self):
        await self.model_manager.stop()

    async def get_subprocess_diarizer(self):
        """Get the subprocess-based diarization model (avoids CUDA conflicts)."""
        if (
            not hasattr(self, "_subprocess_diarizer")
            or self._subprocess_diarizer is None
        ):
            try:
                model_id = (
                    f"subprocess_diarizer_{self.diarization_model_config.model_name}"
                )
                self._subprocess_diarizer = SubprocessDiarizer(
                    model_id, self.diarization_model_config
                )
                await self._subprocess_diarizer.load()
            except Exception as e:
                log.error(f"Failed to load subprocess diarizer: {e}")
                return None
        return self._subprocess_diarizer

    async def get_nemotron_asr(self):
        """Get the Nemotron transcriber."""
        try:
            return await self.model_manager.get_model(
                ModelType.NEMOTRON_ASR, self.nemotron_model_config
            )
        except Exception as e:
            log.error(f"Failed to load Nemotron: {e}")
            return None
