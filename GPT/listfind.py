import os
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import json
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load the CSV file with the store URLs
data_path = 'Filtered_Atlanta_specialty_coffee_roasters.csv'
store_df = pd.read_csv(data_path)

# Load API keys from environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

if not OPENAI_API_KEY or not GOOGLE_API_KEY:
    raise ValueError("OPENAI_API_KEY and GOOGLE_API_KEY must be set in .env file")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Ensure the CSV has the correct column
if 'Website' not in store_df.columns:
    raise ValueError("The CSV file must contain a 'Website' column.")

def create_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.121 Safari/537.36'
    })
    return session

session = create_session()

def fetch_product_links(shop_url):
    """
    Use GPT to identify and extract product links from a shop page.
    """
    try:
        response = session.get(shop_url, timeout=100)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract all anchor tags
        anchor_tags = soup.find_all('a', href=True)
        anchor_tag_texts = [f"Text: {a.get_text(strip=True)}, URL: {a['href']}" for a in anchor_tags]
        anchor_tag_text = "\n".join(anchor_tag_texts)
        #print(anchor_tag_text)
        # Use GPT to identify which links are product links
        prompt = f"""
        Below is a list of anchor tags (text and URLs) extracted from a coffee shop website ({shop_url}).
        Please identify and return the URLs that are possible(even a bit possible) to lead to individual product pages for coffee. If you are unsure still return it.
        Only return a JSON array of valid URLs.

        Anchor Tags:
        {anchor_tag_text}
        """

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        )

        # Extract and clean up GPT response
        response_text = response.choices[0].message.content.strip()
        #print(response_text)

        # Remove any Markdown code block delimiters and explanatory text
        json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if json_match:
            json_text = json_match.group()
            product_links = json.loads(json_text)
            if isinstance(product_links, list) and all(isinstance(link, str) for link in product_links):
                # Convert relative URLs to absolute URLs if needed
                absolute_links = [
                    urljoin(shop_url, link) if not link.startswith("http") else link
                    for link in product_links
                ]
                return absolute_links
            else:
                raise ValueError("GPT response is not a valid JSON list of URLs.")
        else:
            raise ValueError(f"Failed to extract JSON from GPT response: {response_text}")

    except Exception as e:
        print(f"Error fetching product links with GPT: {e}")
        return []



def is_bag_of_coffee(product_url):
    """
    Use GPT to determine if the product is a single bag of roasted coffee.
    """
    try:
        response = session.get(product_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract product title and description
        title = soup.find('title').get_text(strip=True) if soup.find('title') else ""
        description = soup.find('meta', {'name': 'description'})
        description = description['content'] if description else ""

        # Combine title and description for analysis
        product_text = f"Title: {title}\nDescription: {description}"
        prompt = f"""
        Based on the following product details, is this item likely to be a single bag of roasted coffee that is not part of a bundle or subscription and does not include flavored coffees?
        Provide a yes or no answer without explanation. Your answer should just be yes or no. \n\n{product_text}
        """

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        response_text = response.choices[0].message.content.strip()
        print(response_text)
        return response_text.lower().startswith("yes")
    except Exception as e:
        print(f"Error determining product type for {product_url}: {e}")
        return False

output_file = 'filtered_coffee_products_Atlanta.csv'

coffee_products = []

for index, row in store_df.iterrows():
    shop_url = row['Website']
    print(f"Processing shop: {shop_url}")
    product_links = fetch_product_links(shop_url)
    print(len(product_links))
    if product_links:
        for product_link in product_links:
            if is_bag_of_coffee(product_link):
                print(f"Found coffee bag at: {product_link}")
                coffee_products.append({
                    'Shop Website': shop_url,
                    'Product Link': product_link
                })
    else:
        print(f"No product links found for {shop_url}")

coffee_df = pd.DataFrame(coffee_products)
coffee_df.to_csv(output_file, index=False)
print(f"Coffee product links saved to {output_file}")
