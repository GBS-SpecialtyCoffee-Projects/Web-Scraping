#!/bin/bash

CSV_FILE="${1:-cities.csv}"
LIMIT=10
mkdir -p logs

if [ ! -f "$CSV_FILE" ]; then
    echo "Error: CSV file '$CSV_FILE' not found"
    exit 1
fi

# Read first LIMIT cities from CSV (skip header), each row: city,state,country
COUNT=0
tail -n +2 "$CSV_FILE" | head -n "$LIMIT" | while IFS=',' read -r city state country; do
    # Trim whitespace
    city=$(echo "$city" | xargs)
    state=$(echo "$state" | xargs)
    country=$(echo "$country" | xargs)

    # Default country to US if empty
    [ -z "$country" ] && country="US"

    if [ -z "$city" ]; then
        continue
    fi

    COUNT=$((COUNT + 1))
    SESSION="city_${COUNT}_${city// /_}_${state}"
    CMD="source venv/bin/activate && python3 shopfind.py \"$city\" --state \"$state\" --country \"$country\" --log-file \"${city}_${state}.log\"; echo 'DONE'; read"

    tmux new-session -d -s "$SESSION" "$CMD"
    echo "[$COUNT] Started tmux session '$SESSION': $city, $state, $country"
done

echo "Launched $COUNT tmux sessions — use 'tmux ls' to see them"
