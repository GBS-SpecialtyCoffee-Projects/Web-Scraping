from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
import random
from openai import OpenAI


def traversetags(tag):
    text = ""
    try:
        if tag.string is not None and len(list(tag.descendants)) < 1:
            if tag.string.strip() != '':
                text = tag.string.strip() + "\n"
                return text
        else:
            for child in tag.children:
                result = traversetags(child)
                if result:
                    text += result
    except Exception as e:
        if tag.string and tag.string.strip() != '':
            text = tag.string.strip() + "\n"
            return text

    return text


def fetchtext(url):
    options = webdriver.ChromeOptions()

    options.add_argument('--headless')  # Run Chrome in headless mode
    options.add_argument('--no-sandbox')  # Optional but often useful in headless environments
    options.add_argument('--disable-dev-shm-usage')  # Optional for avoiding shared memory issues

    options.browser_version = 'stable'
    options.platform_name = 'any'
    options.accept_insecure_certs = True

    driver = webdriver.Chrome(options=options)

    notfound_bool = False
    redirected_bool = False

    temp_line = ""
    main_text = ""


    intended_url = url
    driver.get(intended_url)
    driver.implicitly_wait(3)

    # Get the current URL after the page has loaded
    current_url = driver.current_url

    # Check if a redirection occurred
    if current_url != intended_url:
        # print(f"Page was redirected to: {current_url}")
        redirected_bool = True
        intended_url = current_url
        #check if the page is in english or not 
    
    language = driver.find_element(By.TAG_NAME, "html").get_attribute("lang")
    # print(language)

    if not (language and language.startswith("en")):
        driver.get("https://translate.google.com/")

        website_button = driver.find_element(By.XPATH,"//button[@aria-label='Website translation']")
        website_button.click()
        time.sleep(2)

        input_box = driver.find_element(By.XPATH,"//input[@type='url']")
        input_box.send_keys(intended_url)  # Replace with the URL of the page you want to translate
        input_box.send_keys(Keys.RETURN)

        time.sleep(3)

        driver.switch_to.window(driver.window_handles[1])

        time.sleep(7)

        html_content = driver.page_source
    else:
        html_content = driver.page_source

    print(html_content)

    soup = BeautifulSoup(html_content, 'html.parser')

    print("html parsed")

    # get the body of the html document
    if soup.body is not None:
        body = soup.body
    else:
        body = soup

    if body.find('main') is not None:
        body = body.main

    # remove nav,header and footer elements from the body
    for tag in body.find_all(['nav', 'header', 'footer','style','script']):
        tag.decompose()

    # remove elements that have class with the word "header" in them
    for tag in body.find_all(class_=re.compile("header")):
            tag.decompose()

    for tag in body.find_all(id=re.compile("header")):
        tag.decompose()

    for tag in body.find_all(id=re.compile("menu")):
        tag.decompose()

    for tag in body.find_all(class_=re.compile("hidden")):
        tag.decompose()

    for tag in body.find_all(id=re.compile("hidden")):
        tag.decompose()

    for tag in body.find_all(class_=re.compile("nav")):
        tag.decompose()

    for tag in body.find_all(id=re.compile("nav")):
        tag.decompose()

    # recurivsely go through the body and print elements 

    text = intended_url + "\n"

    for tag in body.children:
        text = text + traversetags(tag) + "\n"

    # print(text)
        
    driver.close()


    # pattern_404 = re.compile(r'not found|404')
    pattern_404 = re.compile(r"(?i)404[\s\-:]*.*?(Page\s+)?Not\s+Found")
    pattern_related = re.compile(r'related product|also bought|similar product|ordered with')

    lines = text.splitlines()
    for line in lines:
        if(pattern_404.search(line)):
            # print('this page returned 404')
            # main_text = '404'
            notfound_bool = True
            break
        elif(pattern_related.search(line)):
            # print('this page has related products')
            break
        else:
            if temp_line.strip() == line.strip():
                continue
            else:
                temp_line = line
                main_text = main_text + line + "\n"
    
    # print(main_text)
    return main_text,notfound_bool,redirected_bool
    # return html_content


def refine_text(text):
    client = OpenAI(
    # base_url = "https://integrate.api.nvidia.com/v1",
    # api_key = "$API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC"
    api_key = "OPEN_AI_KEY",
    )
    

    completion = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
    {
        "role": "user",
        "content": f"""This is a block of scraped text from a coffee product listing page.

Your job is to isolate and clean up the **main product listing of the page only** — do not touch or include any other products or unrelated text (e.g. ads, headers, navigation).

Keep all the important information *intact and verbatim* as much as possible: product name (which is generally at the top of the page), origin, prices, size, tasting notes, roaster, grower, processing method, and description.

DO NOT summarize or rephrase. Just lightly clean (e.g. fix broken words, remove junk HTML or scripts), and preserve the product details exactly as they appeared.

Then Extract the following answers from the cleaned product:
   - product_name - usually the name of the coffee found at the top of the page and corresponds to the last part of the URL in the text
   - country_of_origin - if the page references multiple countries the answer should be "blend"
   - price
   - unit - This should contain the size of coffee available and its unit of measurement
   - grower_name

**Additionally**, from the cleaned product text, identify and collect all sentences that contain the word "organic". Gather these sentences into a list.

Return the output as a valid JSON object in the following format:

{{
  "refined_text": "...",  // cleaned first product block
  "extracted_data": {{
    "product_name": "...",
    "country_of_origin": "...",
    "price": "...",
    "unit": "...",
    "grower_name": "..."
  }},
  "organic_sentences": [
    "...",
    "..."
  ]
}}

Here is the raw text:
{text}
"""
    }
],
   temperature=0.3,
    top_p=1,
    max_tokens=1048,
    frequency_penalty=0,
    presence_penalty=0,
    response_format={
        "type": "json_object"
    }
)


    # text = ""

    # for chunk in completion:
    #     if chunk.choices[0].delta.content is not None:
    #         text = text + chunk.choices[0].delta.content
    #         # print(chunk.choices[0].delta.content, end="")
        
    # return text
    return completion.choices[0].message.content
           
def analyze(url):
    bodystring,notfound_bool,redirected_bool = fetchtext(url)
    if not notfound_bool and not redirected_bool:
        if len(bodystring) > 1000:
            refined_text = refine_text(bodystring)
            return refined_text
    else:
        return f'page not found or redirected verify this here {url}'
    




