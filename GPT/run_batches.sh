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
CMDS=""
while IFS=',' read -r city state country; do
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
    [ "$COUNT" -gt "$LIMIT" ] && break

    CMDS="$CMDS && echo '[$COUNT] Running: $city, $state, $country' && python3 shopfind.py \"$city\" --state \"$state\" --country \"$country\" --log-file \"${city}_${state}.log\""
done < <(tail -n +2 "$CSV_FILE")

# Build full command with venv activation
FULL_CMD="source venv/bin/activate $CMDS; echo 'ALL DONE'; read"

tmux new-session -d -s "shopfind_run" "$FULL_CMD"
echo "Started tmux session 'shopfind_run' with $COUNT cities — use 'tmux attach -t shopfind_run' to watch"
