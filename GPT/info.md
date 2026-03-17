```
python shopfind.py "Atlanta" --log-file atlanta_run.log


 # US city with state (recommended)
  python shopfind.py "Atlanta" --state GA

  # Disambiguate Portland
  python shopfind.py "Portland" --state OR
  python shopfind.py "Portland" --state ME

  # Multiple cities, same state
  python shopfind.py "Denver" "Boulder" --state CO

  # Non-US country
  python shopfind.py "Melbourne" --country Australia

  # All options together
  python shopfind.py "Atlanta" --state GA --country US --log-file atlanta.log
```