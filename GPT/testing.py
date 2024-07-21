from bs4 import BeautifulSoup
import requests
import openai
import json
import requests

# Initialize the OpenAI API key
openai.api_key = "youropenai key"

# Function to scrape website content
def scrape_website(url):
    response = requests.get(url)
    if response.status_code == 200:
        return response.text
    else:
        return None

# Function to preprocess HTML content
def preprocess_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    main_content = soup.get_text(separator="\n")
    return main_content

# Define the initial prompt template
initial_prompt = """
    Extract the following information from the HTML content:
    - name (e.g: 200 Degrees Coffee Roastery)
    - first wave (e.g: 1, 2 ,3, NA)
    - coffees (e.g: 8, 5 ,10)
    - city (e.g: NOTTINGHAM)
    - country (e.g United Kingdom)
    - shop_site (e.g: https://200degs.com/collections/coffee-beans/)
    - google_rating (e.g:4.9)
    - google_reviews (e.g:22)
    - website (e.g: https://200degs.com)
    - facebook (e.g: https://www.facebook.com/200DegreesCoffee)
    - instagram (e.g: https://www.instagram.com/200degs/)
    - latitude (e.g: 53)
    - longitude (e.g: -1)

    HTML: {html_content}
    """

# Function to extract information using OpenAI's chat.completions.create
def extract_information(html_content):
    preprocessed_content = preprocess_html(html_content)
    prompt = initial_prompt.format(html_content=preprocessed_content)
    
    
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=2048,
        temperature=0.5,
        top_p=1
    )
    return response.choices[0].message.content
    

# Example usage
if __name__ == "__main__":
    url = "https://europeancoffeetrip.com/roaster/3fe/"
    html_content = scrape_website(url)
    if html_content:
        extracted_info = extract_information(html_content)
        print(extracted_info)
    else:
        print("Failed to retrieve website content.")



url = "https://woodgrousecoffee.com/shop/ana-sora-microlot"
html_content = scrape_website(url)
if html_content:
    extracted_info = extract_information(html_content)
    print(extracted_info)
else:
    print("Failed to retrieve website content.")


url = "https://workshopcoffee.com/products/la-pllata-decaf"
html_content = scrape_website(url)
if html_content:
    extracted_info = extract_information(html_content)
    print(extracted_info)
else:
    print("Failed to retrieve website content.")
