#!/bin/bash
export PYTHONUNBUFFERED=1

echo "Starting minicast server..."
python minicast_async.py &

echo "Starting main bot process..."
python main.py
