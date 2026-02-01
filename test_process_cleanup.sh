#!/bin/bash

# Test script to verify process cleanup functionality
# This script tests that child processes are properly cleaned up when the server exits

set -e

echo "🧪 Testing Process Cleanup for AI CLI Bridge"
echo "=============================================="
echo ""

# Function to count processes
count_processes() {
    ps aux | grep -E "(claude|codex|server.py)" | grep -v grep | wc -l | tr -d ' '
}

# Function to check for orphan processes
check_orphans() {
    ps -ef | grep -E "(claude|codex)" | grep "PPID 1" | grep -v grep | wc -l | tr -d ' '
}

echo "📊 Initial process count:"
INITIAL_COUNT=$(count_processes)
echo "   Processes: $INITIAL_COUNT"
echo ""

echo "🚀 Starting test scenario..."
echo "   This will start the server and then exit it to test cleanup"
echo ""

# Test 1: Normal exit
echo "Test 1: Testing normal exit (Ctrl+C)"
echo "--------------------------------------"
echo "Starting server in background..."

# Start server in background
python server.py &
SERVER_PID=$!
echo "   Server PID: $SERVER_PID"

# Wait a bit for server to start
sleep 2

# Send SIGINT (Ctrl+C)
echo "   Sending SIGINT (Ctrl+C)..."
kill -INT $SERVER_PID 2>/dev/null || true

# Wait for cleanup
sleep 3

# Check process count
AFTER_COUNT=$(count_processes)
echo "   Process count after exit: $AFTER_COUNT"

# Check for orphans
ORPHAN_COUNT=$(check_orphans)
echo "   Orphan processes: $ORPHAN_COUNT"

if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "   ✅ No orphan processes found"
else
    echo "   ❌ Found $ORPHAN_COUNT orphan processes"
    ps -ef | grep -E "(claude|codex)" | grep "PPID 1" | grep -v grep
fi

echo ""

# Test 2: SIGTERM
echo "Test 2: Testing SIGTERM signal"
echo "-------------------------------"
echo "Starting server in background..."

# Start server in background
python server.py &
SERVER_PID=$!
echo "   Server PID: $SERVER_PID"

# Wait a bit for server to start
sleep 2

# Send SIGTERM
echo "   Sending SIGTERM..."
kill -TERM $SERVER_PID 2>/dev/null || true

# Wait for cleanup
sleep 3

# Check process count
AFTER_COUNT=$(count_processes)
echo "   Process count after exit: $AFTER_COUNT"

# Check for orphans
ORPHAN_COUNT=$(check_orphans)
echo "   Orphan processes: $ORPHAN_COUNT"

if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "   ✅ No orphan processes found"
else
    echo "   ❌ Found $ORPHAN_COUNT orphan processes"
    ps -ef | grep -E "(claude|codex)" | grep "PPID 1" | grep -v grep
fi

echo ""

# Final check
echo "📊 Final process count:"
FINAL_COUNT=$(count_processes)
echo "   Processes: $FINAL_COUNT"
echo ""

if [ "$FINAL_COUNT" -le "$INITIAL_COUNT" ]; then
    echo "✅ Process cleanup test PASSED"
    echo "   No process leaks detected"
else
    echo "❌ Process cleanup test FAILED"
    echo "   Process count increased from $INITIAL_COUNT to $FINAL_COUNT"
    echo "   Leaked processes: $((FINAL_COUNT - INITIAL_COUNT))"
fi

echo ""
echo "🔍 To manually verify, run:"
echo "   ps aux | grep -E '(claude|codex|server.py)' | grep -v grep"
echo "   ps -ef | grep -E '(claude|codex)' | grep 'PPID 1'"
