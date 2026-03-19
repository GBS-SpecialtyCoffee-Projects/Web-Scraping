import os
import time
import logging
import json
import argparse
import re
from dataclasses import dataclass
from typing import Optional, Set, List
from datetime import timedelta
import pandas as pd
import requests
import urllib3
from urllib.parse import urlparse, urljoin
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================
# TIMING UTILITY
# ============================
class Timer:
    """Simple timer for tracking operation duration"""
    def __init__(self, name: str = "Operation"):
        self.name = name
        self.start_time = None
        self.elapsed = 0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed = time.time() - self.start_time
        logger.info(f"{self.name} took {self._format_time()}")

    def _format_time(self) -> str:
        if self.elapsed < 1:
            return f"{self.elapsed:.2f}s"
        mins = int(self.elapsed // 60)
        secs = self.elapsed % 60
        return f"{mins}m {secs:.1f}s"

    def get_formatted(self) -> str:
        return self._format_time()


# ============================
# LOGGING SETUP
# ============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================
# CONFIGURATION
# ============================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
MAX_DEPTH = 5
REQUEST_TIMEOUT = 30
SCRAPE_TIMEOUT = 15
GPT_MODEL = 'gpt-4o-mini'
METRO_CACHE_FILE = 'metro_area_cache.json'
MASTER_LIST_FILE = os.path.join('roasters', 'master_list.csv')

QUERIES = [
    'specialty coffee roaster',
    'artisan coffee roaster'
]

# Anchor-text patterns that indicate a shop/order page exists
SHOP_LINK_RE = re.compile(
    r'\b(shop|store|order|buy|purchase|subscribe|subscription|merch|products|collections)\b',
    re.IGNORECASE,
)

# Map country names/codes to Google Places API region codes (ISO 3166-1 alpha-2)
COUNTRY_TO_REGION = {
    'us': 'us', 'usa': 'us', 'united states': 'us',
    'ca': 'ca', 'canada': 'ca',
    'uk': 'gb', 'united kingdom': 'gb', 'gb': 'gb',
    'au': 'au', 'australia': 'au',
    'nz': 'nz', 'new zealand': 'nz',
    'de': 'de', 'germany': 'de',
    'fr': 'fr', 'france': 'fr',
    'it': 'it', 'italy': 'it',
    'es': 'es', 'spain': 'es',
    'jp': 'jp', 'japan': 'jp',
    'kr': 'kr', 'south korea': 'kr',
    'br': 'br', 'brazil': 'br',
    'mx': 'mx', 'mexico': 'mx',
    'co': 'co', 'colombia': 'co',
}


def country_to_region(country: str) -> str:
    """Convert country name or code to Google API region code"""
    region = COUNTRY_TO_REGION.get(country.lower())
    if region:
        return region
    # If it's already a 2-letter code, use it directly
    if len(country) == 2:
        return country.lower()
    logger.warning(f"Unknown country '{country}', defaulting to 'us'")
    return 'us'

def get_province_or_state(formatted_address: str) -> Optional[str]:
    """Extract province/state abbreviation from a Google formatted address.

    Handles patterns like:
      - "Edmonton, AB T5K 0R8, Canada"  -> "AB"
      - "Atlanta, GA 30301, USA"         -> "GA"
      - "123 Main St, Toronto, ON M5V, Canada" -> "ON"
    """
    # Canadian pattern: ", XX postal_code" where postal_code is letter-digit-letter
    ca_match = re.search(r',\s*([A-Z]{2})\s+[A-Z]\d[A-Z]', formatted_address)
    if ca_match:
        return ca_match.group(1)

    # US pattern: ", XX ZIP" where ZIP is 5 digits
    us_match = re.search(r',\s*([A-Z]{2})\s+\d{5}', formatted_address)
    if us_match:
        return us_match.group(1)

    # Fallback: look for ", XX," or ", XX " with 2-letter uppercase code
    fallback_match = re.search(r',\s*([A-Z]{2})\s*[,\s]', formatted_address)
    if fallback_match:
        return fallback_match.group(1)

    return None


CITIES = ['Atlanta']

if not OPENAI_API_KEY or not GOOGLE_API_KEY:
    raise ValueError("OPENAI_API_KEY and GOOGLE_API_KEY must be set in .env file")

# ============================
# DATA STRUCTURES
# ============================
@dataclass
class PlaceInfo:
    """Information about a place from Google Places API"""
    place_id: str
    name: str
    address: str
    website: str
    canonical_domain: Optional[str] = None


@dataclass
class CoffeeRoaster:
    """Final coffee roaster record with store page"""
    name: str
    address: str
    original_website: str
    store_website: str
    source_city: str
    sub_city: str
    state: Optional[str] = None


@dataclass
class RejectedPlace:
    """A place that was skipped during filtering, with the reason"""
    name: str
    address: str
    website: str
    source_city: str
    sub_city: str
    reason: str
    state: Optional[str] = None


# ============================
# SESSION FACTORY
# ============================
def create_session() -> requests.Session:
    """Create a requests session with retry strategy"""
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ============================
# CACHE MANAGEMENT
# ============================
def load_metro_cache(cache_file: str = METRO_CACHE_FILE) -> dict:
    """Load metropolitan area cache from file"""
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Could not load cache file '{cache_file}': {e}")
    return {}


def save_metro_cache(cache: dict, cache_file: str = METRO_CACHE_FILE) -> None:
    """Save metropolitan area cache to file"""
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Saved metro cache to {cache_file}")
    except IOError as e:
        logger.error(f"Failed to save cache file '{cache_file}': {e}")


def load_master_list(filepath: str = MASTER_LIST_FILE) -> tuple:
    """Load master list and return (unique_names, unique_domains)"""
    unique_names = set()
    unique_domains = set()
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath)
            unique_names = set(df['Name'].dropna())
            unique_domains = set(df['domain'].dropna())
            logger.info(f"Loaded master list: {len(unique_names)} names, {len(unique_domains)} domains")
        except Exception as e:
            logger.warning(f"Could not load master list: {e}")
    return unique_names, unique_domains


def update_master_list(roasters: list, rejects: list, filepath: str = MASTER_LIST_FILE) -> None:
    """Append new entries to the master list"""
    new_rows = []
    for r in roasters:
        domain = GooglePlacesAPI.canonicalize_website(r.original_website)
        new_rows.append({
            'Name': r.name, 'domain': domain, 'Address': r.address,
            'Website': r.original_website, 'source_city': r.source_city,
            'sub_city': r.sub_city, 'state': r.state,
            'status': 'accepted', 'reason': ''
        })
    for r in rejects:
        domain = GooglePlacesAPI.canonicalize_website(r.website)
        new_rows.append({
            'Name': r.name, 'domain': domain, 'Address': r.address,
            'Website': r.website, 'source_city': r.source_city,
            'sub_city': r.sub_city, 'state': r.state,
            'status': 'rejected', 'reason': r.reason
        })
    if not new_rows:
        return

    new_df = pd.DataFrame(new_rows)
    if os.path.exists(filepath):
        existing_df = pd.read_csv(filepath)
        combined = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(filepath, index=False)
    logger.info(f"Updated master list: {len(new_rows)} new entries, {len(combined)} total")


# ============================
# GOOGLE PLACES API
# ============================
class GooglePlacesAPI:
    """Handle Google Places API interactions"""

    BASE_URL_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    BASE_URL_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

    def __init__(self, api_key: str, session: requests.Session):
        self.api_key = api_key
        self.session = session
        self.search_count = 0
        self.details_count = 0

    def search(self, query: str, region: str, next_page_token: Optional[str] = None,
               location: Optional[tuple] = None, radius: Optional[int] = None) -> Optional[dict]:
        """Search for places

        Args:
            query: Search query string
            region: Region bias code (e.g., 'ca', 'us')
            next_page_token: Token for paginated results
            location: Optional (lat, lng) tuple to bias results toward
            radius: Optional radius in meters (used with location)
        """
        params = {'query': query, 'region': region, 'key': self.api_key}
        if location:
            params['location'] = f"{location[0]},{location[1]}"
        if radius:
            params['radius'] = radius
        if next_page_token:
            params['pagetoken'] = next_page_token
        try:
            response = self.session.get(self.BASE_URL_SEARCH, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.search_count += 1
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for query '{query}': {e}")
            return None

    def get_details(self, place_id: str) -> Optional[dict]:
        """Get detailed information about a place"""
        params = {
            'place_id': place_id,
            'fields': 'name,formatted_address,website',
            'key': self.api_key
        }
        try:
            response = self.session.get(self.BASE_URL_DETAILS, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.details_count += 1
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching details for place ID '{place_id}': {e}")
            return None

    def geocode_city(self, city: str, region: str) -> Optional[tuple]:
        """Get lat/lng for a city using Google Geocoding API"""
        params = {
            'address': city,
            'region': region,
            'key': self.api_key
        }
        try:
            response = self.session.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params=params, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()
            status = data.get('status', 'UNKNOWN')
            if status != 'OK':
                logger.warning(f"Geocoding API status '{status}' for '{city}' "
                               f"(hint: ensure Geocoding API is enabled in Google Cloud Console)")
                return None
            if data.get('results'):
                loc = data['results'][0]['geometry']['location']
                logger.info(f"Geocoded '{city}' -> ({loc['lat']}, {loc['lng']})")
                return (loc['lat'], loc['lng'])
            logger.warning(f"No geocoding results for '{city}'")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Geocoding request failed for '{city}': {e}")
            return None

    @staticmethod
    def canonicalize_website(url: str) -> Optional[str]:
        """Extract canonical domain from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception as e:
            logger.error(f"Error parsing URL '{url}': {e}")
            return None

    def extract_place_info(self, place: dict) -> Optional[PlaceInfo]:
        """Extract relevant information from a Google place result"""
        place_id = place.get('place_id')
        name = place.get('name')
        address = place.get('formatted_address', 'No address provided')

        website = place.get('website', '')
        if not website:
            details = self.get_details(place_id)
            if details and 'result' in details:
                website = details['result'].get('website', '')
        website = website or 'No website provided'

        canonical_domain = self.canonicalize_website(website) if website != 'No website provided' else None

        return PlaceInfo(
            place_id=place_id,
            name=name,
            address=address,
            website=website,
            canonical_domain=canonical_domain
        )


# ============================
# WEB SCRAPER
# ============================
class WebScraper:
    """Handle website scraping with Beautiful Soup first, Playwright as fallback"""

    MIN_TEXT_LENGTH = 200

    def __init__(self, context, session: requests.Session):
        self.context = context
        self.session = session

    def _parse_html(self, html: str) -> tuple[Optional[str], Optional[BeautifulSoup]]:
        """Parse raw HTML into text content and soup"""
        soup = BeautifulSoup(html, 'html.parser')

        text_soup = BeautifulSoup(html, 'html.parser')
        for tag in text_soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        text = text_soup.get_text(separator=' ', strip=True)[:3000]

        return text, soup

    def _scrape_bs4(self, url: str) -> tuple[Optional[str], Optional[BeautifulSoup]]:
        """Try scraping with requests + Beautiful Soup (fast, no JS)"""
        try:
            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                )
            }
            response = self.session.get(url, headers=headers, timeout=SCRAPE_TIMEOUT, verify=False)
            response.raise_for_status()
            return self._parse_html(response.text)
        except Exception as e:
            logger.warning(f"BS4 scrape failed for {url}: {e}")
            return None, None

    def _scrape_playwright(self, url: str) -> tuple[Optional[str], Optional[BeautifulSoup]]:
        """Scrape with Playwright for JS-rendered content"""
        page = self.context.new_page()
        Stealth().apply_stealth_sync(page)
        try:
            page.goto(url, timeout=SCRAPE_TIMEOUT * 1000, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            html = page.content()
            return self._parse_html(html)
        except Exception as e:
            logger.error(f"Playwright scrape failed for {url}: {e}")
            return None, None
        finally:
            try:
                page.route("**/*", lambda route: route.abort())
                page.close()
            except Exception:
                pass

    def scrape(self, url: str) -> tuple[Optional[str], Optional[BeautifulSoup]]:
        """Scrape website: try Beautiful Soup first, fall back to Playwright"""
        text, soup = self._scrape_bs4(url)

        if text and len(text.strip()) >= self.MIN_TEXT_LENGTH:
            logger.info(f"BS4 scrape successful for {url} ({len(text.strip())} chars)")
            return text, soup

        if text:
            logger.info(f"BS4 returned insufficient content ({len(text.strip())} chars) for {url}, trying Playwright")
        else:
            logger.info(f"BS4 failed for {url}, trying Playwright")

        return self._scrape_playwright(url)

    def get_anchor_tags(self, soup: BeautifulSoup, base_url: str, limit: int = 100) -> List[tuple[str, str]]:
        """Extract anchor tag texts and URLs from soup"""
        anchors = soup.find_all('a', href=True)[:limit]
        result = []
        for a in anchors:
            text = a.get_text(strip=True)
            url = urljoin(base_url, a['href'])
            result.append((text, url))
        return result


# ============================
# GPT ANALYZER
# ============================
class GPTAnalyzer:
    """Handle GPT-based analysis with centralized request logic"""

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    def _gpt_request(self, prompt: str, temperature: float = 0.3, max_tokens: int = 500) -> str:
        """Base method for all GPT requests"""
        try:
            resp = self.client.chat.completions.create(
                model=GPT_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return resp.choices[0].message.content.strip().lower()
        except Exception as e:
            logger.error(f"Error with GPT request: {e}")
            return ""

    def has_shop_links(self, anchor_tags: List[tuple[str, str]]) -> bool:
        """Quick heuristic: check if any anchor text or URL path suggests a shop/order page"""
        for text, url in anchor_tags:
            if SHOP_LINK_RE.search(text):
                return True
            path = urlparse(url).path
            if SHOP_LINK_RE.search(path):
                return True
        return False

    def analyze_roaster(self, website_text: str,
                        anchor_tags: Optional[List[tuple[str, str]]] = None) -> bool:
        """Determine if site offers a way to buy coffee beans online"""
        anchor_summary = ""
        if anchor_tags:
            link_texts = [text for text, _ in anchor_tags if text.strip()][:30]
            if link_texts:
                anchor_summary = (
                    f"\n\nNavigation/link texts found on the page: {', '.join(link_texts)}"
                )

        prompt = (
            f"Based on the following website content, does this business sell or offer "
            f"coffee beans for purchase online — whether through their own shop page, "
            f"an order form, a subscription service, or a link to a third-party store "
            f"(e.g. Square, Shopify, etc.)?\n\n"
            f"Website Content: {website_text}"
            f"{anchor_summary}\n\n"
            f'Respond ONLY with a JSON object in this exact format: {{"shop": "yes" or "no"}}'
        )
        resp = self._gpt_request(prompt, temperature=0)

        try:
            result = json.loads(resp)
            return result.get("shop", "no") == "yes"
        except json.JSONDecodeError:
            logger.warning(f"Could not parse GPT response as JSON: {resp}")
            return "yes" in resp

    def is_shopping_hub(self, anchor_tags: List[tuple[str, str]]) -> bool:
        """Determine if a page is a central shopping hub for coffee"""
        if not anchor_tags:
            return False

        lines = [f"Text: {text}, URL: {url}" for text, url in anchor_tags]
        prompt = (
            "Below is a list of anchor tags (text and URL) from a webpage. "
            "We want to determine if this page serves as the main shopping hub for coffee beans—"
            "that is, a page aggregating many links to individual product listings. "
            "If it's just a single product page, an about page, a blog, or only links out "
            "without listing multiple products, it does NOT count.\n\n"
            + "\n".join(lines) +
            "\nQuestion: Based on these links alone, is this page a central shopping hub "
            "that aggregates many coffee-bean product listings? Answer 'yes' or 'no' without explanation."
        )
        resp = self._gpt_request(prompt, temperature=0, max_tokens=50)
        return "yes" in resp

    def has_coffee_keywords(self, text: str) -> bool:
        """Quick check if text mentions coffee-related keywords"""
        keywords = [
            'coffee', 'roast', 'beans', 'espresso', 'brew',
            'barista', 'arabica', 'robusta', 'specialty coffee',
            'single origin', 'grind', 'blend', 'cafe', 'café'
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)

    def pick_next_link(self, anchor_tags: List[tuple[str, str]], visited: Set[str]) -> Optional[str]:
        """Pick the most likely store page from available links"""
        unvisited = [(text, url) for text, url in anchor_tags if url not in visited]
        if not unvisited:
            return None

        visited_str = "\n".join(visited) if visited else "(none)"
        choices = [f"Text: {text}, URL: {url}" for text, url in unvisited]

        prompt = (
            "Already visited the following URLs, do NOT pick them again:\n"
            f"{visited_str}\n\n"
            "Below is a list of anchor texts and their URLs from this webpage. "
            "Among these unvisited links, find exactly one link that most likely leads "
            "to an online store selling coffee beans. If none seems relevant, say 'None'.\n\n"
            + "\n".join(choices) +
            "\nYour answer should only be the single best URL or 'None' without extra text."
        )
        resp = self._gpt_request(prompt, temperature=0.3, max_tokens=150)

        if "none" in resp:
            return None
        return resp.strip()

    def discover_metro_cities(self, city: str, max_metro_cities: int = 8,
                              state: Optional[str] = None, country: str = 'US') -> List[str]:
        """Discover cities in a metropolitan area using GPT

        Args:
            city: The city to discover metro area for
            max_metro_cities: Maximum number of cities to include (default 8)
            state: State abbreviation or name for disambiguation
            country: Country code or name (default "US")
        """
        location_parts = [city]
        if state:
            location_parts.append(state)
        location_parts.append(country)
        location_str = ", ".join(location_parts)

        prompt = (
            f"You are an expert in metropolitan areas. "
            f"For the city '{location_str}', list the main cities that are considered part of its core metropolitan area. "
            f"LIMIT YOUR RESPONSE TO A MAXIMUM OF {max_metro_cities} CITIES. "
            f"Focus on the largest and most important cities in the metro area, not suburbs or outlying areas. "
            f"Return ONLY a valid JSON array of city names with proper capitalization. "
            f"Example format: [\"City One\", \"City Two\", \"City Three\"]\n\n"
            f"For '{location_str}', return the list of metropolitan area cities (max {max_metro_cities}):"
        )
        resp = self._gpt_request(prompt, temperature=0.1, max_tokens=300)

        # Try direct JSON parsing first
        try:
            cities = json.loads(resp)
            if isinstance(cities, list):
                cities = cities[:max_metro_cities]  # Enforce limit
                return cities
        except json.JSONDecodeError:
            pass

        # Fallback: try regex extraction of JSON array
        try:
            json_match = re.search(r'\[.*\]', resp, re.DOTALL)
            if json_match:
                cities = json.loads(json_match.group())
                if isinstance(cities, list):
                    cities = cities[:max_metro_cities]  # Enforce limit
                    return cities
        except json.JSONDecodeError:
            pass

        # Ultimate fallback: return original city as single-item list
        logger.warning(f"Could not parse metro cities for '{city}' from GPT response, using city alone")
        return [city]


# ============================
# COFFEE ROASTER FINDER
# ============================
class CoffeeRoasterFinder:
    """Orchestrate the process of finding and filtering coffee roasters"""

    def __init__(
        self,
        google_api: GooglePlacesAPI,
        scraper: WebScraper,
        gpt_analyzer: GPTAnalyzer
    ):
        self.google_api = google_api
        self.scraper = scraper
        self.gpt_analyzer = gpt_analyzer

    def scrape_raw_places(self, city: str, queries: List[str], region: str,
                          unique_names: Optional[Set[str]] = None,
                          unique_domains: Optional[Set[str]] = None,
                          state: Optional[str] = None,
                          country: str = 'US',
                          location: Optional[tuple] = None,
                          radius: Optional[int] = None,
                          input_city: Optional[str] = None) -> tuple:
        """Scrape places from Google Places API for a city

        Args:
            city: City name to search for
            queries: List of search queries
            region: Region code
            unique_names: Pre-existing set of roaster names to skip (for deduplication across metro area)
            unique_domains: Pre-existing set of domains to skip (for deduplication across metro area)
            state: State abbreviation or name (e.g., "GA", "AB")
            country: Country code or name (default "US")
            location: Optional (lat, lng) tuple to bias Google search results
            radius: Optional radius in meters for location bias

        Returns:
            Tuple of (accepted places list, rejected places list)
        """
        all_places = []
        rejected_places = []
        if unique_names is None:
            unique_names = set()
        if unique_domains is None:
            unique_domains = set()

        # Build location prefix: "Atlanta, GA, US" or "Atlanta, US"
        location_parts = [city]
        if state:
            location_parts.append(state)
        location_parts.append(country)
        location_str = ", ".join(location_parts)

        for q in queries:
            full_query = f"{q} in {location_str}"
            logger.info(f"Searching for '{full_query}'")
            next_page_token = None

            while True:
                data = self.google_api.search(full_query, region, next_page_token,
                                              location=location, radius=radius)
                if not data or 'results' not in data:
                    break

                for place in data['results']:
                    # Early dedup using fields already in search response
                    name = place.get('name')

                    if name in unique_names:
                        continue

                    place_info = self.google_api.extract_place_info(place)
                    if not place_info:
                        continue

                    # Skip places without websites
                    if place_info.website == 'No website provided':
                        continue

                    # Domain dedup
                    if place_info.canonical_domain and place_info.canonical_domain in unique_domains:
                        continue

                    unique_names.add(place_info.name)
                    if place_info.canonical_domain:
                        unique_domains.add(place_info.canonical_domain)
                    all_places.append(place_info)
                    logger.info(f"  Added: {place_info.name}")
                    time.sleep(1)

                next_page_token = data.get('next_page_token')
                if not next_page_token:
                    break
                time.sleep(2)

        return all_places, rejected_places

    def find_store_page(self, root_url: str, max_depth: int = MAX_DEPTH) -> Optional[str]:
        """Iteratively find the coffee store page starting from root URL"""
        visited: Set[str] = set()
        current = root_url

        best_match = None

        for depth in range(max_depth):
            if current in visited:
                break

            visited.add(current)
            logger.info(f"[Depth {depth+1}] Checking: {current}")

            _, soup = self.scraper.scrape(current)
            if not soup:
                return best_match

            anchor_tags = self.scraper.get_anchor_tags(soup, current)

            # If BS4 found very few links, retry with Playwright to catch JS-rendered nav
            if len(anchor_tags) < 5:
                logger.info(f"Only {len(anchor_tags)} links from BS4, retrying with Playwright")
                _, pw_soup = self.scraper._scrape_playwright(current)
                if pw_soup:
                    anchor_tags = self.scraper.get_anchor_tags(pw_soup, current)

            # Check if this page is a shopping hub
            if self.gpt_analyzer.is_shopping_hub(anchor_tags):
                logger.info(f"Found coffee-selling page at depth {depth+1}: {current}")
                best_match = current
                # Always check depth 2 even if depth 1 matched, but stop after that
                if depth >= 1:
                    return best_match

            # Pick next link
            next_url = self.gpt_analyzer.pick_next_link(anchor_tags, visited)
            if not next_url:
                return best_match
            current = next_url

        return best_match

    def process_city(self, city: str, unique_names: Optional[Set[str]] = None,
                     unique_domains: Optional[Set[str]] = None,
                     state: Optional[str] = None, country: str = 'US',
                     location: Optional[tuple] = None,
                     radius: Optional[int] = None,
                     input_city: Optional[str] = None) -> tuple:
        """Process a city: scrape, filter, and find store pages

        Args:
            city: City name to process (metro sub-city)
            unique_names: Set of roaster names already found (to avoid duplicates)
            unique_domains: Set of domains already found (to avoid duplicates)
            state: State abbreviation or name
            country: Country code or name (default "US")
            location: Optional (lat, lng) tuple to bias Google search results
            radius: Optional radius in meters for location bias
            input_city: The main city from the CLI arguments (source_city)

        Returns:
            Tuple of (accepted roasters, rejected places)
        """
        source_city = input_city or city
        logger.info(f"=== PROCESSING CITY: {city} ===")

        # Scrape raw places with deduplication tracking and geographic filtering
        region = country_to_region(country)
        places, geo_rejects = self.scrape_raw_places(
            city, QUERIES, region, unique_names, unique_domains,
            state=state, country=country, location=location, radius=radius,
            input_city=source_city
        )

        # Filter and find store pages
        filtered_roasters = []
        rejected_places = list(geo_rejects)
        for place in places:
            logger.info(f"Processing {place.website}")
            website_text, soup = self.scraper.scrape(place.website)
            if not website_text:
                rejected_places.append(RejectedPlace(
                    name=place.name, address=place.address, website=place.website,
                    source_city=source_city, sub_city=city, reason="scrape_failed",
                    state=state
                ))
                continue

            # Extract anchor tags for shop detection
            anchor_tags = self.scraper.get_anchor_tags(soup, place.website) if soup else []

            # If BS4 found few links, retry with Playwright to catch JS-rendered nav
            if len(anchor_tags) < 5:
                logger.info(f"Only {len(anchor_tags)} links from BS4 for shop detection, retrying with Playwright")
                pw_text, pw_soup = self.scraper._scrape_playwright(place.website)
                if pw_soup:
                    anchor_tags = self.scraper.get_anchor_tags(pw_soup, place.website)
                if pw_text and not website_text:
                    website_text = pw_text

            has_shop = self.gpt_analyzer.has_shop_links(anchor_tags)

            # If nav has shop links, skip keyword check — the place clearly sells something
            if not has_shop and not self.gpt_analyzer.has_coffee_keywords(website_text):
                logger.info(f"Skipped (no coffee keywords): {place.website}")
                rejected_places.append(RejectedPlace(
                    name=place.name, address=place.address, website=place.website,
                    source_city=source_city, sub_city=city, reason="no_coffee_keywords",
                    state=state
                ))
                continue

            # Fast heuristic: if anchor texts contain shop-like keywords, skip GPT shop check
            if has_shop:
                logger.info(f"Shop link detected in nav for {place.website}, skipping GPT shop check")
                offers_shop = True
            else:
                # Analyze roaster with anchor tag context for broader detection
                offers_shop = self.gpt_analyzer.analyze_roaster(website_text, anchor_tags)

            if not offers_shop:
                logger.info(f"Skipped (no shop): {place.website}")
                rejected_places.append(RejectedPlace(
                    name=place.name, address=place.address, website=place.website,
                    source_city=source_city, sub_city=city, reason="no_shop",
                    state=state
                ))
                continue

            # Find the actual store page
            store_page = self.find_store_page(place.website)
            if store_page:
                roaster = CoffeeRoaster(
                    name=place.name,
                    address=place.address,
                    original_website=place.website,
                    store_website=store_page,
                    source_city=source_city,
                    sub_city=city,
                    state=state
                )
                filtered_roasters.append(roaster)
            else:
                logger.info(f"Skipped (no store page found): {place.website}")
                rejected_places.append(RejectedPlace(
                    name=place.name, address=place.address, website=place.website,
                    source_city=source_city, sub_city=city, reason="no_store_page",
                    state=state
                ))

        return filtered_roasters, rejected_places

    def save_results(self, roasters: List[CoffeeRoaster], city: str):
        """Save filtered roasters to CSV"""
        if roasters:
            df = pd.DataFrame([
                {
                    'Name': r.name,
                    'Address': r.address,
                    'Original Website': r.original_website,
                    'Website': r.store_website,
                    'source_city': r.source_city,
                    'sub_city': r.sub_city,
                    'state': r.state
                }
                for r in roasters
            ])
            output_file = f"Filtered_{city}_specialty_coffee_roasters.csv"
            df.to_csv(output_file, index=False)
            logger.info(f"Saved filtered results to {output_file}")


# ============================
# CLI AND ORCHESTRATION
# ============================
def ensure_folder(folder: str) -> str:
    """Create a folder if it doesn't exist"""
    if not os.path.exists(folder):
        os.makedirs(folder)
        logger.info(f"Created {folder} directory")
    return folder


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Find specialty coffee roasters in metropolitan areas'
    )
    parser.add_argument(
        'cities',
        nargs='+',
        help='One or more city names (e.g., "San Francisco" "New York")'
    )
    parser.add_argument(
        '--state',
        type=str,
        default=None,
        help='US state abbreviation or name (e.g., GA, "New York")'
    )
    parser.add_argument(
        '--country',
        type=str,
        default='US',
        help='Country code or name (default: US)'
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default=None,
        help='Optional log file path to save logs (e.g., --log-file run1.log)'
    )
    return parser.parse_args()


def get_metro_cities(city: str, gpt_analyzer: GPTAnalyzer, cache: dict,
                     state: Optional[str] = None, country: str = 'US') -> List[str]:
    """Get all cities in a metropolitan area, using cache when available"""
    # Include state in cache key to avoid collisions (e.g., Portland OR vs ME)
    cache_key = f"{city}, {state}".lower() if state else city.lower()

    if cache_key in cache:
        logger.info(f"Found {cache_key} in cache (metro: {cache[cache_key]})")
        return cache[cache_key]

    logger.info(f"Discovering metropolitan area for {cache_key}...")
    metro_cities = gpt_analyzer.discover_metro_cities(city, state=state, country=country)
    cache[cache_key] = metro_cities
    logger.info(f"Discovered metro cities for {cache_key}: {metro_cities}")

    return metro_cities


# ============================
# MAIN EXECUTION
# ============================
def setup_file_logging(log_file: str) -> None:
    """Add a file handler to the root logger"""
    file_handler = logging.FileHandler(log_file, mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    logging.getLogger().addHandler(file_handler)
    logger.info(f"Logging to file: {log_file}")


def main():
    script_timer = Timer("=== TOTAL SCRIPT EXECUTION ===")
    script_timer.__enter__()

    try:
        # Parse command line arguments
        args = parse_arguments()
        input_cities = args.cities
        state = args.state
        country = args.country

        # Set up file logging if requested
        if args.log_file:
            logs_folder = ensure_folder("logs")
            log_path = os.path.join(logs_folder, args.log_file)
            setup_file_logging(log_path)

        logger.info(f"Location context: state={state}, country={country}")

        # Initialize components
        session = create_session()
        google_api = GooglePlacesAPI(GOOGLE_API_KEY, session)
        gpt_analyzer = GPTAnalyzer(OPENAI_API_KEY)

        # Launch Playwright browser
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        logger.info("Launched headless browser with stealth context")

        scraper = WebScraper(context, session)
        finder = CoffeeRoasterFinder(google_api, scraper, gpt_analyzer)

        # Load metro cache and ensure roasters folder exists
        metro_cache = load_metro_cache()
        roasters_folder = ensure_folder("roasters")

        # Load master list for cross-run dedup
        global_unique_names, global_unique_domains = load_master_list()

        # Accumulate all new results for master list update
        all_new_roasters: List[CoffeeRoaster] = []
        all_new_rejects: List[RejectedPlace] = []

        # Process each input city and its metropolitan area
        city_timings = []
        for input_city in input_cities:
            city_timer = Timer(f"Processing metro area: {input_city}")
            city_timer.__enter__()

            try:
                metro_cities = get_metro_cities(input_city, gpt_analyzer, metro_cache,
                                                state=state, country=country)

                # Geocode the main input city once for the entire metro area
                region = country_to_region(country)
                geo_location = google_api.geocode_city(
                    f"{input_city}, {state}" if state else input_city, region
                )
                geo_radius = 100000  # 100km radius bias
                if geo_location:
                    logger.info(f"Using location bias: {geo_location} with radius {geo_radius}m")
                else:
                    logger.warning(f"Could not geocode '{input_city}', proceeding without location bias")
                    geo_radius = None

                # Accumulate all roasters and rejects from all cities in the metro area
                all_metro_roasters: List[CoffeeRoaster] = []
                all_metro_rejects: List[RejectedPlace] = []

                for city in metro_cities:
                    roasters, rejects = finder.process_city(
                        city, global_unique_names, global_unique_domains,
                        state=state, country=country,
                        location=geo_location, radius=geo_radius,
                        input_city=input_city
                    )
                    all_metro_roasters.extend(roasters)
                    all_metro_rejects.extend(rejects)

                # Save all metro area roasters in one file
                if all_metro_roasters:
                    output_path = os.path.join(roasters_folder, f"{input_city}_specialty_coffee_roasters.csv")
                    df = pd.DataFrame([
                        {
                            'Name': r.name,
                            'Address': r.address,
                            'Original Website': r.original_website,
                            'Website': r.store_website,
                            'source_city': r.source_city,
                            'sub_city': r.sub_city,
                            'state': r.state
                        }
                        for r in all_metro_roasters
                    ])
                    df.to_csv(output_path, index=False)
                    logger.info(f"Saved {len(all_metro_roasters)} roasters to {output_path}")
                else:
                    logger.info(f"No roasters found for {input_city} metro area")

                # Save rejects to a separate file
                if all_metro_rejects:
                    rejects_path = os.path.join(roasters_folder, f"{input_city}_rejected.csv")
                    df_rejects = pd.DataFrame([
                        {
                            'Name': r.name,
                            'Address': r.address,
                            'Website': r.website,
                            'source_city': r.source_city,
                            'sub_city': r.sub_city,
                            'state': r.state,
                            'reason': r.reason
                        }
                        for r in all_metro_rejects
                    ])
                    df_rejects.to_csv(rejects_path, index=False)
                    logger.info(f"Saved {len(all_metro_rejects)} rejects to {rejects_path}")

                all_new_roasters.extend(all_metro_roasters)
                all_new_rejects.extend(all_metro_rejects)
            finally:
                city_timer.__exit__(None, None, None)
                city_timings.append((input_city, city_timer.elapsed))

        # Update master list with new entries
        update_master_list(all_new_roasters, all_new_rejects)

        # Save metro cache
        save_metro_cache(metro_cache)

        # Print timing summary
        logger.info("\n" + "="*60)
        logger.info("TIMING SUMMARY")
        logger.info("="*60)
        for city, elapsed in city_timings:
            mins = int(elapsed // 60)
            secs = elapsed % 60
            logger.info(f"  {city}: {mins}m {secs:.1f}s")

        # Print API usage summary
        logger.info("\n" + "="*60)
        logger.info("API USAGE SUMMARY")
        logger.info("="*60)
        logger.info(f"  Text Search calls:   {google_api.search_count}  (${google_api.search_count * 0.032:.2f})")
        logger.info(f"  Place Details calls: {google_api.details_count}  (${google_api.details_count * 0.017:.2f})")
        total_cost = google_api.search_count * 0.032 + google_api.details_count * 0.017
        logger.info(f"  Estimated total:     ${total_cost:.2f}")

    finally:
        # Clean up browser
        context.close()
        browser.close()
        pw.stop()
        logger.info("Closed headless browser")
        script_timer.__exit__(None, None, None)


if __name__ == "__main__":
    main()
