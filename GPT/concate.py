import pandas as pd
import glob

# Find all CSV files that start with 'Filtered_' and end with '.csv'
csv_files = glob.glob("Filtered_*_specialty_coffee_roasters.csv")

# List to hold individual DataFrames
dfs = []

# Read and append each file
for file in csv_files:
    df = pd.read_csv(file)
    df['source_city'] = file.replace("Filtered_", "").replace("_specialty_coffee_roasters.csv", "")
    dfs.append(df)

# Concatenate all DataFrames into one
combined_df = pd.concat(dfs, ignore_index=True)

# Save the combined CSV
combined_df.to_csv("combined_specialty_coffee_roasters.csv", index=False)

print("✔️ All files combined into 'combined_specialty_coffee_roasters.csv'")

# Load the combined file
df = pd.read_csv("combined_specialty_coffee_roasters.csv")

# Compute counts per city
city_counts = df['source_city'].value_counts().to_dict()

# Sort the cities by their counts (ascending)
cities_sorted = sorted(city_counts.keys(), key=lambda c: city_counts[c])

# Re-build the DataFrame: all rows for the smallest-group city first, then next, …
ordered_chunks = [df[df['source_city'] == city] for city in cities_sorted]
df_grouped = pd.concat(ordered_chunks, ignore_index=True)

# Save out
df_grouped.to_csv("combined_grouped_by_city_count.csv", index=False)

print("✅ Saved reordered file as 'combined_grouped_by_city_count.csv'")
