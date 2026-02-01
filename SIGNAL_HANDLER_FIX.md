# Signal Handler and Lock Fixes

## Issues Fixed

### HIGH Priority Issues

#### 1. Signal Handler Event Loop Conflict (server.py:160, :163, :172)

**Problem:**
- Signal handlers were calling `loop.run_until_complete(cleanup_all_processes())` while `asyncio.run(main())` was already running
- This caused `RuntimeError: This event loop is already running`
- Cleanup was skipped, leading to process leaks

**Solution:**
- **POSIX systems**: Use `loop.add_signal_handler()` to register async signal handlers directly on the running event loop
- **Windows**: Use `loop.call_soon_threadsafe()` to schedule cleanup on the running loop from the signal handler
- Both approaches avoid creating a new event loop while one is already running
- The cleanup is scheduled as a task on the existing event loop using `asyncio.ensure_future()`

**Code Changes:**
```python
# Before (BROKEN):
def signal_handler(signum: int, frame: Any) -> None:
    loop = asyncio.new_event_loop()  # ❌ Creates new loop while main loop running
    asyncio.set_event_loop(loop)
    loop.run_until_complete(cleanup_all_processes())  # ❌ RuntimeError!
    loop.close()

# After (FIXED):
# POSIX:
loop.add_signal_handler(signal.SIGTERM, signal_handler_async)  # ✅ Runs on existing loop

# Windows:
def signal_handler_sync(signum: int, frame: Any) -> None:
    if _main_loop and _main_loop.is_running():
        _main_loop.call_soon_threadsafe(cleanup_and_exit)  # ✅ Schedules on existing loop
```

### MEDIUM Priority Issues

#### 2. Non-Reentrant Lock Causing Deadlock (clink/agents/base.py:28, :49, server.py:144)

**Problem:**
- Used `threading.Lock()` which is not reentrant
- If a signal arrives while holding the lock (e.g., inside `register_process()` or `unregister_process()`), the signal handler tries to acquire the same lock
- This causes a deadlock, freezing the shutdown process

**Solution:**
- Replaced `threading.Lock()` with `threading.RLock()` (reentrant lock)
- A reentrant lock can be acquired multiple times by the same thread without deadlocking
- This allows signal handlers to safely call cleanup functions even if the lock is already held

**Code Changes:**
```python
# Before (BROKEN):
_process_lock = threading.Lock()  # ❌ Not reentrant, can deadlock

# After (FIXED):
_process_lock = threading.RLock()  # ✅ Reentrant, safe for signal handlers
```

## Platform-Specific Handling

### POSIX (Linux, macOS)
- Uses `loop.add_signal_handler()` for efficient, native async signal handling
- Signal handlers run directly on the event loop without thread synchronization overhead
- More efficient and safer than `signal.signal()`

### Windows
- Uses `signal.signal()` with `loop.call_soon_threadsafe()`
- Windows doesn't support `loop.add_signal_handler()`
- `call_soon_threadsafe()` safely schedules cleanup from the signal handler thread to the event loop thread

## Testing

All code quality checks pass:
- ✅ Linting (ruff)
- ✅ Formatting (black)
- ✅ Import sorting (isort)
- ✅ Unit tests (16 passed)

## Key Improvements

1. **No RuntimeError**: Signal handlers no longer try to create event loops while one is running
2. **No Deadlock**: Reentrant lock prevents deadlock when signals arrive during lock-holding operations
3. **Proper Cleanup**: Processes are now properly cleaned up on shutdown
4. **Cross-Platform**: Works correctly on both POSIX and Windows systems
5. **Thread-Safe**: Uses `call_soon_threadsafe()` for safe cross-thread communication

## References

- [asyncio.AbstractEventLoop.add_signal_handler()](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.add_signal_handler)
- [asyncio.AbstractEventLoop.call_soon_threadsafe()](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.call_soon_threadsafe)
- [threading.RLock](https://docs.python.org/3/library/threading.html#threading.RLock)
