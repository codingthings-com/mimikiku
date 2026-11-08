import log_privacy


def test_text_hidden_by_default(monkeypatch):
    monkeypatch.delenv("LOG_TRANSCRIPTS", raising=False)
    line = log_privacy.segment(0, "Hello hello, what's going on?", 2.4, 4.56)
    assert line == "Speaker 0 (2.40s-4.56s): 5 words"
    assert "Hello" not in line


def test_text_shown_when_enabled(monkeypatch):
    monkeypatch.setenv("LOG_TRANSCRIPTS", "1")
    line = log_privacy.segment(1, "Hello there", 0.0, 1.5)
    assert line == 'Speaker 1 (0.00s-1.50s): "Hello there"'


def test_only_explicit_values_enable(monkeypatch):
    for value in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("LOG_TRANSCRIPTS", value)
        assert not log_privacy.enabled()
    for value in ("1", "true", "YES"):
        monkeypatch.setenv("LOG_TRANSCRIPTS", value)
        assert log_privacy.enabled()
