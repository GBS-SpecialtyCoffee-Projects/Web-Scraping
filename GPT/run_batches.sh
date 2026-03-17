#!/bin/bash

COUNTRY="US"
mkdir -p logs

# Each entry: "city,state"
CITIES=(
    "Atlanta,GA"
    "Savannah,GA"
    "Austin,TX"
    "Denver,CO"
    # ... add all 100
)

BATCH_SIZE=10
BATCH_NUM=0

for ((i=0; i<${#CITIES[@]}; i+=BATCH_SIZE)); do
    BATCH_NUM=$((BATCH_NUM + 1))
    CMD="source venv/bin/activate"

    for ((j=i; j<i+BATCH_SIZE && j<${#CITIES[@]}; j++)); do
        IFS=',' read -r city state <<< "${CITIES[j]}"
        CMD="$CMD && python3 shopfind.py \"$city\" --state \"$state\" --country $COUNTRY --log-file logs/batch_${BATCH_NUM}.log"
    done

    tmux new-session -d -s "batch_${BATCH_NUM}" "$CMD; echo 'DONE'; read"
    echo "Started batch $BATCH_NUM"
done

echo "Launched $BATCH_NUM sessions — use 'tmux ls' to see them"
