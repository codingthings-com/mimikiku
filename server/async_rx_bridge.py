"""
Async-aware Rx operator utilities for handling async functions in Rx pipelines.

This module provides utilities to bridge synchronous Rx operators with async functions,
ensuring proper coroutine handling and error management.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional, Union
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

import rx
import rx.operators as ops
from rx import Observable
from rx.core import Observer
from rx.disposable import Disposable, CompositeDisposable

log = logging.getLogger(__name__)


class AsyncOperationTimeoutError(Exception):
    """Raised when an async operation times out."""
    pass


class AsyncOperationCancelledError(Exception):
    """Raised when an async operation is cancelled."""
    pass


class AsyncRxBridge:
    """
    Bridge class for handling async functions in Rx pipelines.
    
    This class provides utilities to properly integrate async functions
    with synchronous Rx operators, ensuring coroutines are awaited
    and errors are handled appropriately.
    """
    
    def __init__(self, 
                 default_timeout: Optional[float] = 30.0,
                 max_concurrent_operations: int = 10):
        """
        Initialize the AsyncRxBridge.
        
        Args:
            default_timeout: Default timeout for async operations in seconds
            max_concurrent_operations: Maximum number of concurrent async operations
        """
        self.default_timeout = default_timeout
        self.max_concurrent_operations = max_concurrent_operations
        self._active_tasks = set()
        self._semaphore = asyncio.Semaphore(max_concurrent_operations)
        
    async def map_async(self, 
                       async_func: Callable, 
                       *args, 
                       timeout: Optional[float] = None,
                       **kwargs) -> Any:
        """
        Execute an async function with proper error handling and timeout.
        
        Args:
            async_func: The async function to execute
            *args: Positional arguments for the function
            timeout: Timeout in seconds (uses default if None)
            **kwargs: Keyword arguments for the function
            
        Returns:
            The result of the async function
            
        Raises:
            AsyncOperationTimeoutError: If the operation times out
            AsyncOperationCancelledError: If the operation is cancelled
        """
        timeout = timeout or self.default_timeout
        
        async with self._semaphore:
            try:
                # Create the task
                task = asyncio.create_task(async_func(*args, **kwargs))
                self._active_tasks.add(task)
                
                # Wait for completion with timeout
                if timeout:
                    result = await asyncio.wait_for(task, timeout=timeout)
                else:
                    result = await task
                    
                return result
                
            except asyncio.TimeoutError:
                log.error(f"Async operation timed out after {timeout}s: {async_func.__name__}")
                raise AsyncOperationTimeoutError(f"Operation timed out after {timeout}s")
                
            except asyncio.CancelledError:
                log.info(f"Async operation cancelled: {async_func.__name__}")
                raise AsyncOperationCancelledError("Operation was cancelled")
                
            except Exception as e:
                log.error(f"Async operation failed: {async_func.__name__}: {e}")
                raise
                
            finally:
                # Clean up the task
                if 'task' in locals():
                    self._active_tasks.discard(task)
    
    def create_async_operator(self, 
                            async_func: Callable,
                            timeout: Optional[float] = None,
                            error_handler: Optional[Callable] = None,
                            graceful_degradation: bool = True) -> Callable:
        """
        Create an Rx operator that properly handles async functions.
        
        Args:
            async_func: The async function to wrap
            timeout: Timeout for the async operation
            error_handler: Optional error handler function
            graceful_degradation: Whether to continue pipeline on errors (return None)
            
        Returns:
            An Rx operator that can be used in pipelines
        """
        def async_operator(source: Observable) -> Observable:
            def subscribe(observer: Observer, scheduler=None) -> Disposable:
                disposable = CompositeDisposable()
                pending_tasks = set()
                
                def on_next(value):
                    async def process_value():
                        try:
                            # Handle both single values and tuples (for starmap)
                            if isinstance(value, tuple):
                                # For starmap operations
                                result = await self.map_async(async_func, *value, timeout=timeout)
                            else:
                                # For regular map operations
                                result = await self.map_async(async_func, value, timeout=timeout)
                            
                            observer.on_next(result)
                            
                        except AsyncOperationTimeoutError as e:
                            log.warning(f"Async operation timed out: {e}")
                            if error_handler:
                                try:
                                    error_handler(e)
                                except Exception as handler_error:
                                    observer.on_error(handler_error)
                            elif graceful_degradation:
                                # Continue pipeline with None result for graceful degradation
                                observer.on_next(None)
                            else:
                                observer.on_error(e)
                                
                        except AsyncOperationCancelledError as e:
                            log.info(f"Async operation cancelled: {e}")
                            # Don't propagate cancellation as error unless explicitly requested
                            if error_handler:
                                try:
                                    error_handler(e)
                                except Exception as handler_error:
                                    observer.on_error(handler_error)
                            elif graceful_degradation:
                                # Continue pipeline with None result for graceful degradation
                                observer.on_next(None)
                                    
                        except Exception as e:
                            log.error(f"Async operation error: {e}")
                            if error_handler:
                                try:
                                    error_handler(e)
                                except Exception as handler_error:
                                    observer.on_error(handler_error)
                            elif graceful_degradation:
                                # Continue pipeline with None result for graceful degradation
                                observer.on_next(None)
                            else:
                                observer.on_error(e)
                        finally:
                            # Remove task from pending set
                            if task in pending_tasks:
                                pending_tasks.remove(task)
                    
                    try:
                        # Ensure we have a running event loop
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            log.error("No running event loop - cannot create async task")
                            if graceful_degradation:
                                observer.on_next(None)
                            return
                        
                        # Create and track the task
                        task = asyncio.create_task(process_value())
                        pending_tasks.add(task)
                        
                    except Exception as e:
                        if error_handler:
                            try:
                                error_handler(e)
                            except Exception as handler_error:
                                log.error(f"Error handler failed: {handler_error}")
                                observer.on_error(handler_error)
                        else:
                            observer.on_error(e)
                
                def on_error(error):
                    observer.on_error(error)
                
                def on_completed():
                    # Wait for all pending tasks to complete before signaling completion
                    async def wait_for_completion():
                        if pending_tasks:
                            await asyncio.gather(*pending_tasks, return_exceptions=True)
                        observer.on_completed()
                    
                    if pending_tasks:
                        asyncio.create_task(wait_for_completion())
                    else:
                        observer.on_completed()
                
                def cleanup():
                    # Cancel all pending tasks
                    for task in pending_tasks.copy():
                        if not task.done():
                            task.cancel()
                    pending_tasks.clear()
                
                # Subscribe to the source
                source_subscription = source.subscribe(
                    on_next=on_next,
                    on_error=on_error,
                    on_completed=on_completed,
                    scheduler=scheduler
                )
                
                disposable.add(source_subscription)
                disposable.add(Disposable(cleanup))
                return disposable
            
            return Observable(subscribe)
        
        return async_operator
    

    
    async def cancel_all_operations(self):
        """
        Cancel all active async operations.
        
        This should be called during cleanup to ensure proper resource management.
        """
        if self._active_tasks:
            log.info(f"Cancelling {len(self._active_tasks)} active async operations")
            
            # Cancel all tasks
            for task in self._active_tasks.copy():
                if not task.done():
                    task.cancel()
            
            # Wait for all tasks to complete cancellation
            if self._active_tasks:
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
            
            self._active_tasks.clear()
    
    def get_active_operation_count(self) -> int:
        """Get the number of currently active async operations."""
        return len(self._active_tasks)
    
    def get_stats(self) -> dict:
        """Get statistics about the async bridge."""
        return {
            "active_operations": len(self._active_tasks),
            "max_concurrent_operations": self.max_concurrent_operations,
            "default_timeout": self.default_timeout,
            "available_slots": self._semaphore._value
        }


# Convenience functions for common use cases

def create_async_map_operator(async_func: Callable, 
                            timeout: Optional[float] = None,
                            error_handler: Optional[Callable] = None,
                            max_concurrent: int = 10,
                            graceful_degradation: bool = True) -> Callable:
    """
    Create an async-aware map operator.
    
    Args:
        async_func: The async function to map over values
        timeout: Timeout for async operations
        error_handler: Optional error handler
        max_concurrent: Maximum concurrent operations
        graceful_degradation: Whether to continue pipeline on errors
        
    Returns:
        An Rx operator for use in pipelines
    """
    bridge = AsyncRxBridge(
        default_timeout=timeout,
        max_concurrent_operations=max_concurrent
    )
    return bridge.create_async_operator(async_func, timeout, error_handler, graceful_degradation)


def create_async_starmap_operator(async_func: Callable,
                                timeout: Optional[float] = None,
                                error_handler: Optional[Callable] = None,
                                max_concurrent: int = 10,
                                graceful_degradation: bool = True) -> Callable:
    """
    Create an async-aware starmap operator.
    
    This is specifically designed for functions that expect unpacked tuple arguments,
    similar to the standard starmap operator but with async support.
    
    Args:
        async_func: The async function to starmap over tuples
        timeout: Timeout for async operations
        error_handler: Optional error handler
        max_concurrent: Maximum concurrent operations
        graceful_degradation: Whether to continue pipeline on errors
        
    Returns:
        An Rx operator for use in pipelines
    """
    bridge = AsyncRxBridge(
        default_timeout=timeout,
        max_concurrent_operations=max_concurrent
    )
    return bridge.create_async_operator(async_func, timeout, error_handler, graceful_degradation)


# Global bridge instance for simple use cases
_default_bridge = AsyncRxBridge()


def async_map(async_func: Callable, 
              timeout: Optional[float] = None,
              error_handler: Optional[Callable] = None,
              graceful_degradation: bool = True) -> Callable:
    """
    Simple async map operator using the default bridge.
    
    Args:
        async_func: The async function to map
        timeout: Optional timeout override
        error_handler: Optional error handler
        graceful_degradation: Whether to continue pipeline on errors
        
    Returns:
        An Rx operator
    """
    return _default_bridge.create_async_operator(async_func, timeout, error_handler, graceful_degradation)


def async_starmap(async_func: Callable,
                  timeout: Optional[float] = None, 
                  error_handler: Optional[Callable] = None,
                  graceful_degradation: bool = True) -> Callable:
    """
    Simple async starmap operator using the default bridge.
    
    Args:
        async_func: The async function to starmap
        timeout: Optional timeout override
        error_handler: Optional error handler
        graceful_degradation: Whether to continue pipeline on errors
        
    Returns:
        An Rx operator
    """
    return _default_bridge.create_async_operator(async_func, timeout, error_handler, graceful_degradation)


async def cleanup_default_bridge():
    """Cleanup the default bridge instance."""
    await _default_bridge.cancel_all_operations()