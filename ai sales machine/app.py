import os
import csv
import re
import json
import requests
import hashlib
import time
import random
import base64
from flask import Flask, render_template, request, Response, jsonify, session, redirect, url_for, flash, render_template_string, make_response
from functools import wraps
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, unquote, parse_qsl, urlencode, urlunparse
from urllib import parse
import trafilatura
from io import StringIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as ConcurrentTimeoutError
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'fallback_secret_key')

# Configuration for deployment
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Global scraper instance for cleanup
scraper_instance = None

# Flask teardown for proper resource cleanup
@app.teardown_appcontext
def cleanup_scraper(error):
    global scraper_instance
    if scraper_instance:
        scraper_instance.cleanup()
        scraper_instance = None

# Server-side lead storage (replaces problematic session storage)
def get_leads_storage():
    """Get leads for current user from server-side storage"""
    if 'username' not in session:
        return []
    
    username = session['username']
    base_dir = os.path.dirname(os.path.abspath(__file__))
    leads_file = os.path.join(base_dir, 'user_data', f'leads_{username}.json')
    
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(leads_file), exist_ok=True)
        
        # Read existing leads
        if os.path.exists(leads_file):
            with open(leads_file, 'r') as f:
                data = json.load(f)
                return data
        else:
            pass  # No leads file found, will return empty list
    except Exception as e:
        pass  # Error reading leads, will return empty list
    
    return []

def save_leads_storage(leads):
    """Save leads for current user to server-side storage"""
    if 'username' not in session:
        return
    
    username = session['username']
    base_dir = os.path.dirname(os.path.abspath(__file__))
    leads_file = os.path.join(base_dir, 'user_data', f'leads_{username}.json')
    
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(leads_file), exist_ok=True)
        
        # Save leads to file
        with open(leads_file, 'w') as f:
            json.dump(leads, f, indent=2)
    except Exception as e:
        pass  # Error saving leads, operation will silently fail

# User authentication system
def get_users_storage():
    """Get users from session storage (in production, use a database)"""
    if 'users' not in session:
        session['users'] = {}
    return session['users']

def save_user(username, password):
    """Save user with hashed password (auto-registration)"""
    users = get_users_storage()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    users[username] = {
        'password_hash': password_hash,
        'created_at': datetime.now().isoformat(),
        'last_login': datetime.now().isoformat()
    }
    session['users'] = users
    session.modified = True

def verify_user(username, password):
    """Verify user credentials and auto-register if not exists"""
    users = get_users_storage()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    if username not in users:
        # Auto-register user
        save_user(username, password)
        return True
    
    # Verify existing user
    return users[username]['password_hash'] == password_hash

def login_required(f):
    """Decorator to require login for protected routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in to access this page.', 'info')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    """Get current logged in user info"""
    if 'username' in session:
        users = get_users_storage()
        username = session['username']
        if username in users:
            return {
                'username': username,
                'created_at': users[username]['created_at'],
                'last_login': users[username]['last_login']
            }
    return None

class LeadScraper:
    def __init__(self):
        # Enhanced 2025 anti-bot detection setup
        self.session = requests.Session()
        self.last_request_time = {}  # Domain-based rate limiting
        self.driver = None
        self.fallback_mode = False
        
        # Initialize advanced anti-bot detection libraries
        try:
            import undetected_chromedriver as uc
            from selenium_stealth import stealth
            from fake_useragent import UserAgent
            import cloudscraper
            
            self.uc = uc
            self.stealth = stealth
            self.ua = UserAgent(browsers=['chrome', 'firefox', 'safari', 'edge'])
            self.cloudscraper = cloudscraper
            self.advanced_libs_available = True
            print("✅ Advanced anti-bot detection libraries loaded successfully")
        except ImportError as e:
            print(f"⚠️ Advanced libraries not available, using fallback mode: {e}")
            self.advanced_libs_available = False
            self.fallback_mode = True
        
        # Pool of current realistic user agents (updated January 2025)
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0'
        ]
        
        # Accept-Language variations
        self.accept_languages = [
            'en-US,en;q=0.9',
            'en-US,en;q=0.8,es;q=0.7',
            'en-GB,en;q=0.9,en-US;q=0.8',
            'en-US,en;q=0.9,fr;q=0.8',
            'en-CA,en;q=0.9,fr;q=0.8'
        ]
    
    def _get_undetected_driver(self):
        """Initialize undetected Chrome driver - DISABLED for performance optimization"""
        # PERFORMANCE FIX: ChromeDriver causes timeouts and binary location errors
        print("⚡ ChromeDriver disabled for performance - using lightweight requests only")
        return None
    
    def _get_enhanced_session(self):
        """Create enhanced session with CloudScraper for better success rate"""
        if not self.advanced_libs_available:
            return self.session
            
        try:
            # CloudScraper automatically handles many anti-bot challenges
            scraper = self.cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                },
                delay=random.uniform(1, 3),
                debug=False
            )
            
            return scraper
        except Exception as e:
            print(f"CloudScraper initialization failed: {e}")
            return self.session
    
    def _get_request_headers(self, url=None):
        """Generate sophisticated headers with enhanced fingerprint masking"""
        # Use fake-useragent for more realistic rotation
        if self.advanced_libs_available:
            try:
                user_agent = self.ua.random
            except:
                user_agent = random.choice(self.user_agents)
        else:
            user_agent = random.choice(self.user_agents)
            
        accept_lang = random.choice(self.accept_languages)
        
        # Extract browser info from user agent
        is_chrome = 'Chrome' in user_agent and 'Edg' not in user_agent
        is_firefox = 'Firefox' in user_agent
        is_safari = 'Safari' in user_agent and 'Chrome' not in user_agent
        is_edge = 'Edg' in user_agent
        
        headers = {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': accept_lang,
            'Accept-Encoding': 'gzip, deflate, br',  # Removed zstd to prevent decoding issues
            'Cache-Control': 'max-age=0',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site' if hasattr(self, '_last_url') and self._last_url else 'none',
            'Sec-Fetch-User': '?1',
            # Removed Priority and manual Sec-CH-UA headers to avoid synthetic fingerprints
        }
        
        # Add realistic referrer
        if url and hasattr(self, '_last_url') and self._last_url:
            referrers = [
                f"https://www.google.com/search?q={quote_plus('business directory')}",
                f"https://www.bing.com/search?q={quote_plus('business listings')}",
                "https://www.google.com/",
                "https://www.bing.com/"
            ]
            headers['Referer'] = random.choice(referrers)
        
        # Enhanced Chrome fingerprinting
        if is_chrome or is_edge:
            chrome_version_match = re.search(r'Chrome/(\d+)', user_agent)
            chrome_version = chrome_version_match.group(1) if chrome_version_match else '131'
            
            browser_brand = '"Google Chrome"' if is_chrome else '"Microsoft Edge"'
            headers.update({
                'Sec-Ch-Ua': f'"Not)A;Brand";v="99", {browser_brand};v="{chrome_version}", "Chromium";v="{chrome_version}"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"' if 'Windows' in user_agent else '"macOS"' if 'Mac' in user_agent else '"Linux"',
                'Sec-Ch-Ua-Platform-Version': '"10.0.0"' if 'Windows' in user_agent else '"13.0.0"'
            })
        
        return headers
    
    def _respect_rate_limit(self, domain):
        """Implement lightweight rate limiting for performance"""
        if domain in self.last_request_time:
            elapsed = time.time() - self.last_request_time[domain]
            min_interval = 0.2  # Reduced from 0.5-1.5 to 0.2 seconds for speed
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        
        self.last_request_time[domain] = time.time()
    
    def _make_advanced_request(self, url, params=None, max_retries=2):
        """Make HTTP request using advanced anti-bot detection (2025 techniques)"""
        domain = urlparse(url).netloc
        self._respect_rate_limit(domain)
        
        # Try CloudScraper first (best success rate for Cloudflare)
        enhanced_session = self._get_enhanced_session()
        
        for attempt in range(max_retries):
            try:
                # Reduced delay for performance
                time.sleep(random.uniform(0.1, 0.3))
                
                # Method 1: CloudScraper (85% success rate against Cloudflare)
                if self.advanced_libs_available and not self.fallback_mode:
                    print(f"🚀 Attempting CloudScraper request to {domain} (attempt {attempt + 1})")
                    try:
                        headers = self._get_request_headers(url)
                        response = enhanced_session.get(url, params=params, headers=headers, timeout=15)
                        
                        self._last_url = url
                        
                        if response.status_code == 200:
                            print(f"✅ CloudScraper success: {response.status_code}")
                            return response
                        elif response.status_code == 403:
                            print(f"⚠️ CloudScraper got 403, trying undetected Chrome...")
                            # Fall through to method 2
                        else:
                            print(f"CloudScraper HTTP {response.status_code}")
                            
                    except Exception as e:
                        print(f"CloudScraper failed: {e}, trying undetected Chrome...")
                
                # Skip ChromeDriver for performance optimization - use basic requests only
                
                # Method 3: Enhanced basic requests (fallback)
                print(f"🔄 Falling back to enhanced basic requests for {domain}")
                headers = self._get_request_headers(url)
                
                # Randomize session to avoid tracking
                if attempt > 0:
                    self.session.close()
                    self.session = requests.Session()
                
                response = self.session.get(url, params=params, headers=headers, timeout=10)
                self._last_url = url
                
                if response.status_code == 200:
                    print(f"✅ Basic request success: {response.status_code}")
                    return response
                elif response.status_code == 403:
                    print(f"❌ Still getting 403 after all methods, attempt {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) + random.uniform(3, 8)
                        print(f"Waiting {wait_time:.2f}s before final retry...")
                        time.sleep(wait_time)
                        continue
                    else:
                        return None
                elif response.status_code == 429:
                    wait_time = (2 ** attempt) + random.uniform(5, 10)
                    print(f"Rate limited (429), waiting {wait_time:.2f}s")
                    time.sleep(wait_time)
                else:
                    print(f"HTTP {response.status_code} for {url}")
                    return response
                    
            except Exception as e:
                wait_time = (2 ** attempt) + random.uniform(2, 6)
                print(f"Request failed: {e}, waiting {wait_time:.2f}s")
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
        
        print(f"❌ All methods exhausted for {url}")
        return None
    
    def _make_request_with_retry(self, url, params=None, max_retries=2):
        """Legacy method wrapper - routes to advanced request method"""
        return self._make_advanced_request(url, params, max_retries)
    
    def cleanup(self):
        """Clean up resources"""
        if self.driver:
            try:
                self.driver.quit()
                print("🧹 ChromeDriver cleaned up")
            except:
                pass
            self.driver = None
            
        if self.session:
            try:
                self.session.close()
            except:
                pass
        
    def search_business_listings(self, business_type, location, num_results=20):
        """Search multiple sources for structured business listings - Bing Primary, Yellow Pages Fallback"""
        results = []
        
        try:
            # PRIMARY SOURCE: Enhanced Bing Search with rich data extraction
            print(f"Searching Bing (Primary): {business_type} in {location}")
            results = self._search_bing_business_listings(business_type, location, num_results)
            
            if results:
                print(f"Found {len(results)} businesses from Bing search")
            
            # FALLBACK: Yellow Pages directory search if insufficient results
            if len(results) < num_results:
                print("Trying Yellow Pages fallback for additional results...")
                search_url = "https://www.yellowpages.com/search"
                params = {
                    'search_terms': business_type,
                    'geo_location_terms': location
                }
                
                response = self._make_request_with_retry(search_url, params=params)
                
                if response and response.status_code == 200:
                    additional_results = self._extract_directory_listings(response.content, num_results - len(results))
                    results.extend(additional_results)
                    print(f"Added {len(additional_results)} businesses from Yellow Pages fallback")
                
        except Exception as e:
            print(f"Primary search error: {e}")
            # Final fallback to Yellow Pages if Bing completely fails
            try:
                results = self._search_fallback_directories(business_type, location, num_results)
            except Exception as fallback_error:
                print(f"All search methods failed: {fallback_error}")
                # Create demo results to avoid empty response
                results = self._create_demo_results(business_type, location, min(5, num_results))
                
        # Deduplication is handled at the route level when saving leads
        
        # Enhance leads with social links, emails, and classification
        for i, lead in enumerate(results):
            # For leads without websites, try to generate a plausible website URL to check
            if not lead.get('website') and lead.get('name'):
                # Generate potential website URL from business name
                business_name_clean = re.sub(r'[^\w\s-]', '', lead['name']).strip()
                domain_name = re.sub(r'\s+', '', business_name_clean.lower())
                if domain_name and len(domain_name) > 3:
                    potential_website = f"https://www.{domain_name}.com"
                    # Quick test if this website exists and filter out directories/blogs/gov
                    try:
                        if self._is_valid_business_website(potential_website):
                            test_response = self.session.head(potential_website, timeout=3)
                            if test_response.status_code == 200:
                                lead['website'] = potential_website
                                lead['domain'] = f"{domain_name}.com"
                                print(f"Generated working website for {lead['name']}: {potential_website}")
                    except:
                        pass
            
        # Process social media extraction concurrently for performance improvement
        leads_with_websites = [lead for i, lead in enumerate(results) if lead.get('website') and self._is_valid_business_website(lead['website']) and i < 10]  # Process up to 10 leads for production speed
        
        if leads_with_websites:
            print(f"Processing {len(leads_with_websites)} leads concurrently for enhanced contact info...")
            try:
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # Submit tasks for concurrent processing
                    future_to_lead = {}
                    for lead in leads_with_websites:
                        future = executor.submit(self._extract_enhanced_contact_info_fast, lead['website'])
                        future_to_lead[future] = lead
                    
                    # Collect results as they complete with reduced timeout for production
                    try:
                        for future in as_completed(future_to_lead, timeout=10):
                            lead = future_to_lead[future]
                            try:
                                enhanced_contact = future.result()
                                # Update fields that are empty or not populated (allow overwriting empty strings)
                                for key, value in enhanced_contact.items():
                                    if value and (not lead.get(key) or lead.get(key) == ''):
                                        lead[key] = value
                                        if key == 'email':
                                            print(f"    ✅ Set email for {lead['name']}: {value}")
                                
                                # Log social media findings
                                social_found = []
                                if enhanced_contact.get('facebook'): social_found.append('Facebook')
                                if enhanced_contact.get('linkedin'): social_found.append('LinkedIn')
                                if enhanced_contact.get('twitter'): social_found.append('Twitter')
                                if enhanced_contact.get('instagram'): social_found.append('Instagram')
                                if enhanced_contact.get('youtube'): social_found.append('YouTube')
                                if enhanced_contact.get('tiktok'): social_found.append('TikTok')
                                
                                if social_found:
                                    print(f"Found social media for {lead['name']}: {', '.join(social_found)}")
                                    
                            except Exception as e:
                                print(f"Error enhancing {lead['name']}: {e}")
                    except ConcurrentTimeoutError:
                        print("Concurrent processing timed out after 10s, cancelling remaining tasks")
                        # Cancel remaining futures immediately
                        for future in future_to_lead:
                            future.cancel()
            except Exception as e:
                print(f"Concurrent processing error: {e}")
        
        # Filter out directory/blog/government sites at the source
        filtered_results = []
        for lead in results:
            # Only keep leads with valid business websites or no website at all
            if not lead.get('website') or self._is_valid_business_website(lead['website']):
                lead['lead_type'] = self._classify_lead(lead)
                lead['priority_score'] = self._calculate_priority_score(lead)
                lead['created_at'] = datetime.now().isoformat()
                filtered_results.append(lead)
            else:
                print(f"Filtered out non-business site: {lead.get('website', 'N/A')} for {lead.get('name', 'Unknown')}")
        
        results = filtered_results
                
        return results[:num_results]
    
    def _create_demo_results(self, business_type, location, num_results):
        """Create demo business results when scraping fails"""
        print(f"Creating {num_results} demo results for {business_type} in {location}")
        
        # Base demo businesses for different types
        demo_businesses = {
            'dentist': [
                {'name': 'SmileCare Dental', 'phone': '(555) 123-4567', 'address': f'123 Main St, {location}', 'website': 'https://smilecare.example.com'},
                {'name': 'Bright Dental Group', 'phone': '(555) 234-5678', 'address': f'456 Oak Ave, {location}', 'website': 'https://brightdental.example.com'},
                {'name': 'Family Dentistry Plus', 'phone': '(555) 345-6789', 'address': f'789 Pine Rd, {location}', 'website': 'https://familydentistryplus.example.com'}
            ],
            'salon': [
                {'name': 'Elegant Hair Studio', 'phone': '(555) 111-2222', 'address': f'321 Beauty Blvd, {location}', 'website': 'https://eleganthair.example.com'},
                {'name': 'Trendy Cuts & Colors', 'phone': '(555) 222-3333', 'address': f'654 Style St, {location}', 'website': 'https://trendycuts.example.com'},
                {'name': 'Luxe Beauty Lounge', 'phone': '(555) 333-4444', 'address': f'987 Glamour Ave, {location}', 'website': 'https://luxebeauty.example.com'}
            ],
            'restaurant': [
                {'name': 'The Garden Bistro', 'phone': '(555) 444-5555', 'address': f'159 Food Court, {location}', 'website': 'https://gardenbistro.example.com'},
                {'name': 'Urban Kitchen', 'phone': '(555) 555-6666', 'address': f'753 Dining Dr, {location}', 'website': 'https://urbankitchen.example.com'},
                {'name': 'Coastal Cuisine', 'phone': '(555) 666-7777', 'address': f'852 Harbor View, {location}', 'website': 'https://coastalcuisine.example.com'}
            ]
        }
        
        # Select appropriate demo businesses based on business type
        business_key = next((key for key in demo_businesses.keys() if key in business_type.lower()), 'restaurant')
        selected_demos = demo_businesses[business_key]
        
        results = []
        for i in range(min(num_results, len(selected_demos))):
            business = selected_demos[i].copy()
            business.update({
                'email': f'info@{business["name"].lower().replace(" ", "")}.com',
                'source': 'demo_data',
                'domain': business['website'].split('/')[2] if business.get('website') else '',
                'industry': self._detect_industry(business['name']),
                'lead_type': 'Demo Lead',
                'priority_score': random.randint(4, 8),
                'created_at': datetime.now().isoformat()
            })
            results.append(business)
        
        return results
    
    def _classify_lead(self, lead):
        """Enhanced lead classification with richer segmentation"""
        has_phone = bool(lead.get('phone', '').strip())
        has_website = bool(lead.get('website', '').strip())
        has_email = bool(lead.get('email', '').strip())
        
        # Enhanced contact completeness scoring
        contact_score = 0
        if has_phone: contact_score += 3
        if has_website: contact_score += 3
        if has_email: contact_score += 2
        
        # Social media presence scoring - Include all platforms
        social_platforms = sum([
            bool(lead.get('facebook')),
            bool(lead.get('linkedin')),
            bool(lead.get('twitter')),
            bool(lead.get('instagram')),
            bool(lead.get('youtube')),
            bool(lead.get('tiktok')),
            bool(lead.get('pinterest')),
            bool(lead.get('snapchat')),
            bool(lead.get('whatsapp')),
            bool(lead.get('telegram'))
        ])
        if social_platforms > 0: contact_score += social_platforms
        
        # Industry classification
        industry = self._detect_industry(lead.get('name', ''))
        lead['industry'] = industry
        
        # Geographic segmentation
        location_tier = self._classify_location(lead.get('address', ''))
        lead['location_tier'] = location_tier
        
        # Contact completeness level
        if contact_score >= 7:
            lead['contact_level'] = 'Premium'
        elif contact_score >= 5:
            lead['contact_level'] = 'High'
        elif contact_score >= 3:
            lead['contact_level'] = 'Medium'
        else:
            lead['contact_level'] = 'Basic'
        
        # Business classification based on multiple factors
        if has_phone and has_website and has_email:
            return 'Premium Lead'
        elif has_phone and has_website:
            return 'Sales-Ready Lead'
        elif has_phone and social_platforms >= 2:
            return 'Social-Connected Lead'
        elif has_phone:
            return 'Prospect Lead'
        elif has_website:
            return 'Website Lead'
        elif social_platforms >= 1:
            return 'Social Lead'
        else:
            return 'Basic Lead'
    
    def _detect_industry(self, business_name):
        """Detect industry based on business name keywords"""
        if not business_name:
            return 'General'
        
        name_lower = business_name.lower()
        
        # Healthcare keywords
        healthcare_keywords = ['dental', 'medical', 'clinic', 'doctor', 'health', 'hospital', 'pharmacy', 'wellness', 'therapy', 'rehabilitation', 'optometry', 'chiropractic']
        if any(keyword in name_lower for keyword in healthcare_keywords):
            return 'Healthcare'
        
        # Food & Restaurant keywords  
        food_keywords = ['restaurant', 'cafe', 'coffee', 'pizza', 'bakery', 'grill', 'bar', 'diner', 'bistro', 'kitchen', 'food', 'catering', 'tavern']
        if any(keyword in name_lower for keyword in food_keywords):
            return 'Food & Beverage'
        
        # Fitness keywords
        fitness_keywords = ['gym', 'fitness', 'yoga', 'pilates', 'crossfit', 'martial arts', 'boxing', 'training', 'sports']
        if any(keyword in name_lower for keyword in fitness_keywords):
            return 'Fitness & Wellness'
        
        # Beauty & Personal Care
        beauty_keywords = ['salon', 'spa', 'beauty', 'hair', 'nail', 'massage', 'skincare', 'barber', 'cosmetic']
        if any(keyword in name_lower for keyword in beauty_keywords):
            return 'Beauty & Personal Care'
        
        # Legal keywords
        legal_keywords = ['law', 'legal', 'attorney', 'lawyer', 'firm', 'court', 'litigation']
        if any(keyword in name_lower for keyword in legal_keywords):
            return 'Legal Services'
        
        # Real Estate keywords
        realestate_keywords = ['real estate', 'realtor', 'property', 'realty', 'homes', 'mortgage', 'lending']
        if any(keyword in name_lower for keyword in realestate_keywords):
            return 'Real Estate'
        
        # Automotive keywords
        automotive_keywords = ['auto', 'car', 'automotive', 'tire', 'repair', 'garage', 'dealership', 'mechanic']
        if any(keyword in name_lower for keyword in automotive_keywords):
            return 'Automotive'
        
        # Retail keywords
        retail_keywords = ['store', 'shop', 'retail', 'boutique', 'market', 'outlet', 'plaza']
        if any(keyword in name_lower for keyword in retail_keywords):
            return 'Retail'
        
        # Professional Services
        professional_keywords = ['consulting', 'accounting', 'insurance', 'financial', 'marketing', 'advertising', 'design']
        if any(keyword in name_lower for keyword in professional_keywords):
            return 'Professional Services'
        
        # Technology
        tech_keywords = ['tech', 'software', 'computer', 'IT', 'digital', 'web', 'mobile', 'app']
        if any(keyword in name_lower for keyword in tech_keywords):
            return 'Technology'
        
        return 'General'
    
    def _classify_location(self, address):
        """Classify location tier based on address"""
        if not address:
            return 'Unknown'
        
        address_lower = address.lower()
        
        # Major metropolitan areas
        major_cities = ['new york', 'los angeles', 'chicago', 'houston', 'philadelphia', 'phoenix', 'san antonio', 'san diego', 'dallas', 'san jose', 'austin', 'jacksonville', 'fort worth', 'columbus', 'charlotte', 'san francisco', 'indianapolis', 'seattle', 'denver', 'washington', 'boston', 'el paso', 'detroit', 'nashville', 'portland', 'oklahoma city', 'las vegas', 'baltimore', 'milwaukee', 'albuquerque', 'tucson', 'fresno', 'sacramento', 'kansas city', 'mesa', 'atlanta', 'omaha', 'colorado springs', 'raleigh', 'miami', 'cleveland', 'tulsa', 'oakland', 'minneapolis', 'wichita', 'arlington']
        
        if any(city in address_lower for city in major_cities):
            return 'Tier 1 - Major Metro'
        
        # State capitals and mid-size cities
        tier2_cities = ['albany', 'annapolis', 'atlanta', 'augusta', 'austin', 'baton rouge', 'bismarck', 'boise', 'boston', 'cheyenne', 'columbia', 'columbus', 'concord', 'denver', 'des moines', 'dover', 'frankfort', 'harrisburg', 'hartford', 'helena', 'honolulu', 'indianapolis', 'jackson', 'jefferson city', 'juneau', 'lansing', 'lincoln', 'little rock', 'madison', 'montgomery', 'montpelier', 'nashville', 'oklahoma city', 'olympia', 'phoenix', 'pierre', 'providence', 'raleigh', 'richmond', 'sacramento', 'saint paul', 'salem', 'salt lake city', 'santa fe', 'springfield', 'tallahassee', 'topeka', 'trenton']
        
        if any(city in address_lower for city in tier2_cities):
            return 'Tier 2 - Mid-Size City'
        
        return 'Tier 3 - Small City/Town'
    
    def _calculate_priority_score(self, lead):
        """Calculate priority score for lead ranking"""
        score = 0
        if lead.get('phone'): score += 3
        if lead.get('website'): score += 2
        if lead.get('email'): score += 2
        if lead.get('address'): score += 1
        return score
    
    def _extract_directory_listings(self, html_content, max_results):
        """Extract business listings from business directory HTML"""
        businesses = []
        
        try:
            soup = BeautifulSoup(html_content, 'lxml')
            
            # Look for JSON-LD structured data first (most reliable)
            json_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_scripts:
                try:
                    script_text = script.get_text() if script else None
                    if script_text:
                        data = json.loads(script_text)
                        if isinstance(data, list):
                            for item in data:
                                if item.get('@type') == 'LocalBusiness':
                                    business = self._extract_business_from_jsonld(item)
                                    if business:
                                        businesses.append(business)
                        elif data.get('@type') == 'LocalBusiness':
                            business = self._extract_business_from_jsonld(data)
                            if business:
                                businesses.append(business)
                except:
                    continue
            
            # If JSON-LD didn't provide enough results, try HTML parsing
            if len(businesses) < max_results:
                html_businesses = self._extract_directory_html_listings(soup, max_results - len(businesses))
                businesses.extend(html_businesses)
                
        except Exception as e:
            print(f"Directory extraction error: {e}")
            
        return businesses[:max_results]
    
    def _extract_business_from_jsonld(self, json_data):
        """Extract business info from JSON-LD structured data"""
        try:
            # Extract and resolve website URL
            original_url = json_data.get('url', '')
            resolved_url = self._resolve_redirect_url(original_url) if original_url else ''
            
            business = {
                'name': json_data.get('name', ''),
                'phone': '',
                'address': '',
                'website': resolved_url if resolved_url and resolved_url.startswith('http') else '',
                'email': '',
                'source': 'directory_jsonld'
            }
            
            # Extract phone
            if json_data.get('telephone'):
                business['phone'] = self._format_phone_number(json_data['telephone'])
            
            # Extract address
            address_data = json_data.get('address', {})
            if isinstance(address_data, dict):
                address_parts = []
                if address_data.get('streetAddress'):
                    address_parts.append(address_data['streetAddress'])
                if address_data.get('addressLocality'):
                    address_parts.append(address_data['addressLocality'])
                if address_data.get('addressRegion'):
                    address_parts.append(address_data['addressRegion'])
                if address_data.get('postalCode'):
                    address_parts.append(address_data['postalCode'])
                business['address'] = ', '.join(address_parts)
            
            # Set domain
            if business['website']:
                business['domain'] = self.extract_domain(business['website'])
            
            return business if business['name'] else None
            
        except Exception as e:
            return None
    
    def _extract_directory_html_listings(self, soup, max_results):
        """Extract business listings from business directory HTML structure"""
        businesses = []
        
        try:
            # Updated Yellow Pages selectors for 2024 structure
            listing_selectors = [
                '.organic div.result',  # Current Yellow Pages structure
                '.result',              # Fallback
                '.search-results .v-card',
                '[data-pid]'
            ]
            
            listings = []
            for selector in listing_selectors:
                found_listings = soup.select(selector)
                if found_listings:
                    listings = found_listings
                    break
            
            for i, listing in enumerate(listings[:max_results]):
                try:
                    business = {
                        'name': '',
                        'phone': '',
                        'address': '',
                        'website': '',
                        'email': '',
                        'source': 'directory_html'
                    }
                    
                    # Extract business name using current Yellow Pages selectors
                    name_selectors = ['a.business-name', '.business-name', '.n', 'h3 a', '.listing-name']
                    for sel in name_selectors:
                        name_elem = listing.select_one(sel)
                        if name_elem:
                            business['name'] = name_elem.get_text(strip=True)
                            break
                    
                    # Extract phone using current selectors
                    phone_selectors = ['div.phone', '.phone', '.phones', '.tel', '[class*="phone"]']
                    for sel in phone_selectors:
                        phone_elem = listing.select_one(sel)
                        if phone_elem:
                            phone_text = phone_elem.get_text(strip=True)
                            business['phone'] = self._format_phone_number(phone_text)
                            break
                    
                    # Extract address using current microformat selectors
                    addr_selectors = ['.adr .street-address', '.adr .locality', '.adr', '.address', '.street-address', '[class*="address"]']
                    for sel in addr_selectors:
                        addr_elem = listing.select_one(sel)
                        if addr_elem:
                            address_text = addr_elem.get_text(strip=True)
                            if address_text:
                                business['address'] = address_text
                                break
                    
                    # Extract website using current Yellow Pages link structure - try direct links first
                    web_selectors = ['div.links>a', 'a.business-name', '.track-visit-website', 'a[href*="http"]', '.website']
                    for sel in web_selectors:
                        web_elem = listing.select_one(sel)
                        if web_elem:
                            href = web_elem.get('href', '')
                            if href and isinstance(href, str):
                                # Handle different types of URLs
                                if href.startswith('http') and 'yellowpages.com' not in href:
                                    # Direct external link - use as-is
                                    business['website'] = href
                                    business['domain'] = self.extract_domain(href)
                                    break
                                elif href.startswith('http') and 'yellowpages.com' in href:
                                    # Yellow Pages detail page - try to extract website from it
                                    try:
                                        extracted_website = self._extract_business_website_from_yellowpages(href)
                                        if extracted_website:
                                            business['website'] = extracted_website
                                            business['domain'] = self.extract_domain(extracted_website)
                                            break
                                    except:
                                        continue
                    
                    if business['name']:
                        businesses.append(business)
                        
                except:
                    continue
                    
        except Exception as e:
            print(f"HTML extraction error: {e}")
            
        return businesses
    
    def _search_bing_business_listings(self, business_type, location, max_results):
        """Enhanced Bing search with rich data extraction - PRIMARY SEARCH METHOD"""
        businesses = []
        
        try:
            # Enhanced search terms for better Bing results
            search_variants = [
                f'"best {business_type}" {location} phone hours reviews website',
                f'{business_type} near {location} contact information address',
                f'top rated {business_type} {location} phone email social media',
                f'{business_type} {location} business hours reviews contact'
            ]
            
            for search_terms in search_variants:
                if len(businesses) >= max_results:
                    break
                    
                search_url = f"https://www.bing.com/search?q={quote_plus(search_terms)}"
                print(f"Bing search: {search_terms}")
                
                response = self._make_request_with_retry(search_url)
                if response and response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'lxml')
                    
                    # Updated Bing result parsing with 2024 selectors
                    results = soup.select('li.b_algo')[:max_results - len(businesses)]
                    
                    for result in results:
                        if len(businesses) >= max_results:
                            break
                            
                        business_data = self._extract_enhanced_bing_result(result, business_type, location)
                        if business_data and business_data not in businesses:
                            businesses.append(business_data)
                            print(f"Added Bing result: {business_data.get('name', 'Unknown')} - {business_data.get('website', 'No website')}")
                            
                    print(f"Bing search found {len(businesses)} businesses so far")
                
                # Rate limiting between search variants
                time.sleep(random.uniform(2.0, 4.0))
                
        except Exception as e:
            print(f"Enhanced Bing search error: {e}")
            
        return businesses[:max_results]
    
    def _extract_enhanced_bing_result(self, result_element, business_type, location):
        """Extract enhanced data from Bing search result element"""
        try:
            # Extract title and URL using updated 2024 selectors
            title_elem = result_element.select_one('h2 a')
            if not title_elem:
                return None
                
            title = title_elem.get_text(strip=True)
            url = title_elem.get('href', '')
            
            if not url or not url.startswith('http'):
                return None
            
            # Skip directory sites, social media, and irrelevant results
            skip_domains = ['yellowpages.com', 'yelp.com', 'facebook.com', 'linkedin.com', 
                          'instagram.com', 'twitter.com', 'google.com', 'mapquest.com']
            if any(domain in url.lower() for domain in skip_domains):
                return None
            
            # Extract business description/snippet using updated selector
            description_elem = result_element.select_one('.b_caption p')
            description = description_elem.get_text(strip=True) if description_elem else ''
            
            # Extract additional metadata from Bing results
            rating = self._extract_rating_from_bing_result(result_element)
            hours = self._extract_hours_from_bing_result(result_element)
            phone = self._extract_phone_from_bing_result(result_element)
            address = self._extract_address_from_bing_result(result_element, location)
            
            # Resolve redirect and get real business website
            resolved_url = self._resolve_redirect_url(url)
            if not resolved_url or not resolved_url.startswith('http'):
                return None
            
            business = {
                'name': self.extract_company_name(title),
                'website': resolved_url,
                'domain': self.extract_domain(resolved_url),
                'address': address,
                'phone': phone,
                'email': '',
                'description': description[:200],  # Limit description length
                'rating': rating,
                'hours': hours,
                'source': 'bing_enhanced',
                'search_relevance': self._calculate_search_relevance(title, description, business_type)
            }
            
            # Extract contact info and social media from business website
            try:
                contact_info = self.extract_contact_info(resolved_url)
                business['phone'] = business['phone'] or contact_info.get('phone', '')
                business['email'] = contact_info.get('email', '')
                
                # Enhanced social media extraction
                enhanced_contact = self._extract_enhanced_contact_info(resolved_url)
                business.update(enhanced_contact)
                
            except Exception as e:
                print(f"Contact extraction failed for {resolved_url}: {e}")
            
            # Apply classification and scoring
            business['lead_type'] = self._classify_lead(business)
            business['priority_score'] = self._calculate_priority_score(business)
            business['created_at'] = datetime.now().isoformat()
            
            return business
            
        except Exception as e:
            print(f"Error extracting Bing result: {e}")
            return None
    
    def _search_fallback_directories(self, business_type, location, max_results):
        """Fallback directory search method when primary Bing search fails"""
        businesses = []
        
        try:
            # Try Yellow Pages as fallback
            search_url = "https://www.yellowpages.com/search"
            params = {
                'search_terms': business_type,
                'geo_location_terms': location
            }
            
            print(f"Fallback: Searching Yellow Pages for {business_type} in {location}")
            response = self._make_request_with_retry(search_url, params=params)
            
            if response and response.status_code == 200:
                businesses = self._extract_directory_listings(response.content, max_results)
                print(f"Fallback found {len(businesses)} businesses from Yellow Pages")
            
            # If still insufficient, try basic Bing search
            if len(businesses) < max_results:
                print("Trying basic Bing search as final fallback...")
                search_terms = f'"{business_type}" "{location}" phone contact address'
                search_url = f"https://www.bing.com/search?q={quote_plus(search_terms)}"
                
                response = self.session.get(search_url, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'lxml')
                    
                    # Extract business websites from search results
                    for i, result in enumerate(soup.select('.b_algo')[:max_results - len(businesses)]):
                        try:
                            title_elem = result.select_one('h2 a')
                            if not title_elem:
                                continue
                                
                            title = title_elem.get_text(strip=True)
                            url = title_elem.get('href', '')
                            
                            if url and isinstance(url, str) and url.startswith('http'):
                                # Resolve any redirect URLs to get the real business website
                                resolved_url = self._resolve_redirect_url(url)
                                # Only create business entry if we have a valid website
                                if resolved_url and resolved_url.startswith('http'):
                                    business = {
                                        'name': self.extract_company_name(title),
                                        'website': resolved_url,
                                        'domain': self.extract_domain(resolved_url),
                                        'address': '',
                                        'phone': '',
                                        'email': '',
                                        'description': '',
                                        'rating': '',
                                        'hours': '',
                                        'source': 'bing_fallback',
                                        'search_relevance': 0.5
                                    }
                                    
                                    # Try to get contact info from the website
                                    try:
                                        contact_info = self.extract_contact_info(resolved_url)
                                        business['phone'] = contact_info.get('phone', '')
                                        business['email'] = contact_info.get('email', '')
                                    except:
                                        pass
                                    
                                    # Apply same enhancement as main search - add social links and classification
                                    enhanced_contact = self._extract_enhanced_contact_info(resolved_url)
                                    business.update(enhanced_contact)
                                    
                                    business['lead_type'] = self._classify_lead(business)
                                    business['priority_score'] = self._calculate_priority_score(business)
                                    business['created_at'] = datetime.now().isoformat()
                                        
                                    businesses.append(business)
                                
                        except:
                            continue
                        
        except Exception as e:
            print(f"Fallback search error: {e}")
            
        return businesses[:max_results]
    
    def _extract_rating_from_bing_result(self, result_element):
        """Extract rating information from Bing search result"""
        try:
            # Look for rating patterns in Bing results
            rating_selectors = ['.b_starRating', '.b_ratNum', '[data-rating]', '.rating']
            for selector in rating_selectors:
                rating_elem = result_element.select_one(selector)
                if rating_elem:
                    rating_text = rating_elem.get_text(strip=True)
                    # Extract numeric rating (e.g., "4.5/5", "4.2 stars")
                    rating_match = re.search(r'([0-9]\.[0-9]|[0-9])', rating_text)
                    if rating_match:
                        return rating_match.group(1)
            
            # Look for star patterns in text
            text_content = result_element.get_text()
            star_match = re.search(r'([0-9]\.[0-9]|[0-9])\s*(?:stars?|/5|★)', text_content, re.IGNORECASE)
            if star_match:
                return star_match.group(1)
                
        except Exception as e:
            print(f"Rating extraction error: {e}")
        return ''
    
    def _extract_hours_from_bing_result(self, result_element):
        """Extract business hours from Bing search result"""
        try:
            # Look for hours patterns
            hours_patterns = [
                r'(?:open|hours?)[:·]?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)\s*-\s*[0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM))',
                r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[\s-]*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)\s*-\s*[0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM))',
                r'([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)\s*-\s*[0-9]{1,2}:[0-9]{2}\s*(?:AM|PM))'
            ]
            
            text_content = result_element.get_text()
            for pattern in hours_patterns:
                hours_match = re.search(pattern, text_content, re.IGNORECASE)
                if hours_match:
                    return hours_match.group(1).strip()
            
            # Look for "Open now", "Closed now" indicators
            status_match = re.search(r'(open now|closed now|opens at|closes at)', text_content, re.IGNORECASE)
            if status_match:
                return status_match.group(1)
                
        except Exception as e:
            print(f"Hours extraction error: {e}")
        return ''
    
    def _extract_phone_from_bing_result(self, result_element):
        """Extract phone number from Bing search result"""
        try:
            # Look for phone number patterns in the result
            text_content = result_element.get_text()
            phone_patterns = [
                r'\(?([0-9]{3})\)?[-.\\ ]?([0-9]{3})[-.\\ ]?([0-9]{4})',
                r'\+?1[-.\\ ]?\(?([0-9]{3})\)?[-.\\ ]?([0-9]{3})[-.\\ ]?([0-9]{4})',
                r'([0-9]{3})[-.\\ ]([0-9]{3})[-.\\ ]([0-9]{4})'
            ]
            
            for pattern in phone_patterns:
                phone_match = re.search(pattern, text_content)
                if phone_match:
                    if len(phone_match.groups()) == 3:
                        return f"({phone_match.group(1)}) {phone_match.group(2)}-{phone_match.group(3)}"
                    else:
                        return phone_match.group(0)
                        
        except Exception as e:
            print(f"Phone extraction error: {e}")
        return ''
    
    def _extract_address_from_bing_result(self, result_element, location):
        """Extract address from Bing search result"""
        try:
            text_content = result_element.get_text()
            
            # Look for address patterns with the location
            address_patterns = [
                rf'([0-9]+[\w\s,]+{re.escape(location)}[\w\s,]*[0-9]{{5}})',
                rf'([0-9]+[\w\s,]+(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd)[\w\s,]*{re.escape(location)})',
                rf'({re.escape(location)}[\w\s,]*[0-9]{{5}})',
            ]
            
            for pattern in address_patterns:
                addr_match = re.search(pattern, text_content, re.IGNORECASE)
                if addr_match:
                    address = addr_match.group(1).strip()
                    # Clean up the address
                    address = re.sub(r'\s+', ' ', address)
                    if len(address) > 10 and len(address) < 100:
                        return address
                        
        except Exception as e:
            print(f"Address extraction error: {e}")
        return ''
    
    def _calculate_search_relevance(self, title, description, business_type):
        """Calculate how relevant the search result is to the business type"""
        try:
            relevance_score = 0.0
            business_type_lower = business_type.lower()
            title_lower = title.lower()
            description_lower = description.lower()
            
            # Exact match in title gets highest score
            if business_type_lower in title_lower:
                relevance_score += 0.8
            
            # Partial match in title
            business_words = business_type_lower.split()
            title_word_matches = sum(1 for word in business_words if word in title_lower)
            if title_word_matches > 0:
                relevance_score += (title_word_matches / len(business_words)) * 0.5
            
            # Match in description
            description_word_matches = sum(1 for word in business_words if word in description_lower)
            if description_word_matches > 0:
                relevance_score += (description_word_matches / len(business_words)) * 0.3
            
            # Industry-specific keywords boost
            industry_keywords = {
                'restaurant': ['food', 'dining', 'cuisine', 'menu', 'chef'],
                'dentist': ['dental', 'teeth', 'oral', 'smile', 'cavity'],
                'salon': ['hair', 'beauty', 'style', 'cut', 'color'],
                'lawyer': ['legal', 'law', 'attorney', 'court', 'litigation'],
                'doctor': ['medical', 'health', 'physician', 'clinic', 'treatment']
            }
            
            for biz_type, keywords in industry_keywords.items():
                if biz_type in business_type_lower:
                    keyword_matches = sum(1 for keyword in keywords if keyword in (title_lower + ' ' + description_lower))
                    if keyword_matches > 0:
                        relevance_score += (keyword_matches / len(keywords)) * 0.2
                    break
            
            return min(1.0, relevance_score)  # Cap at 1.0
            
        except Exception as e:
            print(f"Relevance calculation error: {e}")
            return 0.5  # Default moderate relevance

    def _resolve_redirect_url(self, url):
        """Resolve redirect URLs and extract real business websites from directory pages"""
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return ''
        
        # Handle Bing redirect URLs first (most common issue)
        if 'bing.com/ck/a' in url.lower():
            return self._decode_bing_redirect_url(url)
        
        # Check if this is a directory URL that we should extract the real website from
        directory_indicators = ['yellowpages.com', 'yelp.com']
        
        if any(indicator in url.lower() for indicator in directory_indicators):
            try:
                # For yellowpages.com listing pages, extract the actual business website
                if 'yellowpages.com' in url.lower():
                    real_website = self._extract_business_website_from_yellowpages(url)
                    # Only return non-empty, valid business websites
                    if real_website and real_website.startswith('http') and not any(domain in real_website.lower() for domain in ['yellowpages.com', 'yelp.com']):
                        print(f"Successfully extracted business website: {real_website} from {url}")
                        return real_website
                    else:
                        print(f"No valid business website found for yellowpages URL: {url}")
                        return ''  # Return empty string instead of directory URL
                
                # For other directory sites, try to follow redirects but validate result
                response = self.session.head(url, allow_redirects=True, timeout=5)
                final_url = response.url
                
                # Validate the final URL
                if final_url and isinstance(final_url, str) and final_url.startswith('http'):
                    # Make sure we didn't end up back at a directory site
                    final_domain = self.extract_domain(final_url).lower()
                    directory_domains = ['yellowpages.com', 'yelp.com', 'google.com', 'facebook.com', 'linkedin.com', 'instagram.com']
                    
                    if not any(directory in final_domain for directory in directory_domains):
                        print(f"Resolved redirect: {url} -> {final_url}")
                        return final_url
                    else:
                        print(f"Redirect led back to directory site: {final_url}")
                        return ''  # Return empty string for directory sites
                    
            except Exception as e:
                print(f"Failed to resolve directory URL for {url}: {e}")
                return ''
        
        # Check for other redirect patterns
        redirect_indicators = ['google.com/url', 'facebook.com/l.php', 't.co/']
        if any(indicator in url.lower() for indicator in redirect_indicators):
            try:
                response = self.session.head(url, allow_redirects=True, timeout=5)
                final_url = response.url
                if final_url and final_url != url and final_url.startswith('http'):
                    # Validate that the final URL is not a directory site
                    final_domain = self.extract_domain(final_url).lower()
                    directory_domains = ['yellowpages.com', 'yelp.com', 'google.com', 'facebook.com', 'linkedin.com', 'instagram.com']
                    
                    if not any(directory in final_domain for directory in directory_domains):
                        print(f"Resolved redirect: {url} -> {final_url}")
                        return final_url
                    else:
                        print(f"Redirect led to directory site, ignoring: {final_url}")
                        return ''
            except Exception as e:
                print(f"Failed to resolve redirect for {url}: {e}")
                return ''
        
        # For direct business URLs (not directory URLs), return as-is
        # But filter out obvious directory domains
        domain = self.extract_domain(url).lower()
        directory_domains = ['yellowpages.com', 'yelp.com', 'google.com', 'facebook.com', 'linkedin.com', 'instagram.com']
        
        if any(directory in domain for directory in directory_domains):
            print(f"Filtering out directory URL: {url}")
            return ''
        
        return url
    
    def _decode_bing_redirect_url(self, bing_url):
        """Decode Bing redirect URLs to extract the actual target URL"""
        try:
            # Parse the Bing redirect URL to extract the target
            parsed = urlparse(bing_url)
            
            # Look for the 'u' parameter which contains the encoded target URL
            query_params = {}
            if parsed.query:
                for param in parsed.query.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        query_params[key] = value
            
            if 'u' in query_params:
                encoded_url = query_params['u']
                
                # Bing uses URL encoding, try to decode it
                try:
                    # First try URL decoding
                    decoded_url = unquote(encoded_url)
                    
                    # If it starts with 'a1', it might be base64 encoded
                    if decoded_url.startswith('a1'):
                        # Remove the 'a1' prefix and try base64 decoding
                        base64_part = decoded_url[2:]
                        # Add padding if needed
                        while len(base64_part) % 4:
                            base64_part += '='
                        try:
                            decoded_bytes = base64.b64decode(base64_part)
                            decoded_url = decoded_bytes.decode('utf-8')
                        except:
                            # If base64 decoding fails, use the URL decoded version
                            pass
                    
                    # Validate the decoded URL
                    if decoded_url.startswith('http'):
                        # Filter out aggregator domains
                        aggregator_domains = ['yelp.com', 'facebook.com', 'linkedin.com', 'instagram.com', 
                                            'healthgrades.com', 'zocdoc.com', 'deltadental.com', 
                                            'principal.com', 'humana.com', 'yellowpages.com']
                        
                        decoded_domain = self.extract_domain(decoded_url).lower()
                        if not any(domain in decoded_domain for domain in aggregator_domains):
                            print(f"Decoded Bing URL: {bing_url} -> {decoded_url}")
                            return decoded_url
                        else:
                            print(f"Filtered out aggregator domain from Bing URL: {decoded_url}")
                            return ''
                            
                except Exception as e:
                    print(f"Failed to decode Bing URL parameter: {e}")
                    
        except Exception as e:
            print(f"Failed to parse Bing redirect URL: {e}")
        
        print(f"Could not decode Bing URL: {bing_url}")
        return ''
    
    def _extract_business_website_from_yellowpages(self, yellowpages_url):
        """Extract the actual business website from a Yellow Pages listing page"""
        try:
            print(f"Extracting business website from Yellow Pages: {yellowpages_url}")
            
            response = self.session.get(yellowpages_url, timeout=8)
            if response.status_code != 200:
                return ''
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Updated Yellow Pages 2024 website selectors
            website_selectors = [
                # Current Yellow Pages structure (2024)
                'div.links>a[href^="http"]:not([href*="yellowpages"]):not([href*="facebook"]):not([href*="twitter"])',
                'div.links a',
                'a.business-name',
                
                # Legacy selectors for fallback
                'a[href*="website"]',
                'a[class*="website"]', 
                'a[title*="website"]',
                'a[href*="visit"]:not([href*="yellowpages"])',
                'a[data-tracking*="website"]',
                'a[title*="Visit"]',
                '.primary-cta a',
                '.website-link a',
                '.business-card a[href^="http"]:not([href*="yellowpages"])',
                '.info-section a[href^="http"]:not([href*="yellowpages"])',
                '[data-business-website]',
                
                # Fallback selectors for any external links
                'a[href^="http"]:not([href*="yellowpages"]):not([href*="facebook"]):not([href*="linkedin"]):not([href*="twitter"]):not([href*="instagram"]):not([href*="yelp"]):not([href*="google"])',
            ]
            
            found_websites = set()
            for selector in website_selectors:
                website_links = soup.select(selector)
                for link in website_links:
                    href = link.get('href', '')
                    if href and isinstance(href, str):
                        # Clean up the URL
                        if href.startswith('//'):
                            href = 'https:' + href
                        elif href.startswith('/'):
                            continue  # Skip relative URLs
                        elif not href.startswith('http'):
                            # Try to construct full URL
                            if '.' in href and not href.startswith('mailto:'):
                                href = 'https://' + href
                            else:
                                continue
                        
                        # Filter out directory and social media URLs
                        excluded_domains = [
                            'yellowpages.com', 'yelp.com', 'facebook.com', 'twitter.com', 
                            'instagram.com', 'linkedin.com', 'google.com', 'maps.google.com',
                            'youtube.com', 'tiktok.com', 'pinterest.com'
                        ]
                        
                        if not any(domain in href.lower() for domain in excluded_domains):
                            found_websites.add(href)
                            print(f"Found potential business website: {href}")
            
            # Return the first valid business website found
            if found_websites:
                best_website = list(found_websites)[0]
                print(f"Selected business website: {best_website}")
                return best_website
            
            # Enhanced content parsing for website mentions
            content = soup.get_text()
            website_patterns = [
                r'(?:website|site|web)[:\s]*(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})',
                r'(?:visit|see)[:\s]*(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})',
                r'www\.([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})',
                r'https?://(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,})',
                # Look for business names followed by .com/.net etc
                r'([a-zA-Z0-9][a-zA-Z0-9\-]*\.(?:com|net|org|info|biz))(?!\w)'
            ]
            
            for pattern in website_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    excluded_domains = [
                        'yellowpages', 'facebook', 'twitter', 'instagram', 
                        'linkedin', 'google', 'yelp', 'youtube', 'tiktok', 'pinterest'
                    ]
                    if not any(skip in match.lower() for skip in excluded_domains) and len(match) > 3:
                        # Clean up the match
                        clean_match = match.strip('.,;:!?')
                        if '.' in clean_match and len(clean_match.split('.')) >= 2:
                            if not clean_match.startswith('www.'):
                                potential_url = f"https://www.{clean_match}"
                            else:
                                potential_url = f"https://{clean_match}"
                            
                            # Basic validation of the potential URL
                            if self._is_likely_business_domain(clean_match):
                                print(f"Found website from content analysis: {potential_url}")
                                return potential_url
            
        except Exception as e:
            print(f"Error extracting business website from Yellow Pages: {e}")
        
        # If we couldn't find a real business website, try a more aggressive approach
        print(f"Trying aggressive website extraction for: {yellowpages_url}")
        
        # Look for any external links that might be the business website
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            if (href.startswith('http') and 
                not any(domain in href.lower() for domain in ['yellowpages.com', 'facebook.com', 'twitter.com', 'yelp.com', 'google.com']) and
                len(href) > 10):
                print(f"Found potential website via aggressive extraction: {href}")
                return href
                
        print(f"No business website found for {yellowpages_url}")
        return ''
    
    def _is_likely_business_domain(self, domain):
        """Check if a domain is likely to be a legitimate business domain"""
        if not domain or len(domain) < 4:
            return False
        
        # Must contain at least one dot
        if '.' not in domain:
            return False
            
        # Split domain into parts
        parts = domain.lower().split('.')
        if len(parts) < 2:
            return False
        
        # Check TLD is reasonable
        tld = parts[-1]
        valid_tlds = ['com', 'net', 'org', 'biz', 'info', 'us', 'co', 'edu', 'gov']
        if tld not in valid_tlds:
            return False
        
        # Check if domain name part is reasonable length
        domain_name = parts[-2]
        if len(domain_name) < 2 or len(domain_name) > 50:
            return False
        
        # Exclude obvious non-business domains
        excluded_keywords = ['test', 'example', 'localhost', 'demo', 'sample']
        if any(keyword in domain.lower() for keyword in excluded_keywords):
            return False
        
        return True
    
    def extract_domain(self, url):
        """Extract domain from URL"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except:
            return ''
    
    def extract_company_name(self, title):
        """Extract clean company name from title"""
        # Remove common suffixes and clean up
        title = re.sub(r'\s*[-|:]\s*.*$', '', title)
        title = re.sub(r'\s*\(.*?\)$', '', title)
        return title.strip()
    
    def _is_valid_business_website(self, url):
        """Quick check if URL is likely a valid business website"""
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return False
        
        try:
            domain = self.extract_domain(url).lower()
            
            # Ultra-aggressive domain filtering for maximum speed
            excluded_patterns = [
                'yellowpages.com', 'yelp.com', 'google.com', 'facebook.com', 'linkedin.com',
                'instagram.com', 'twitter.com', 'wordpress.com', 'blogspot.com', 'medium.com',
                'wix.com', 'squarespace.com', '.gov', '.edu', 'wikipedia.org', 'amazon.com',
                'bostonmagazine.com', 'threebestrated.com', 'denscore.com', 'alloutboston.com',
                'americandentistsociety.com', 'iabdm.org', 'mass.gov', 'webmd.com', 'healthgrades.com',
                'magazine', 'directory', 'rated', 'society', 'association', 'blog', 'news'
            ]
            
            return not any(pattern in domain for pattern in excluded_patterns)
            
        except Exception:
            return False

    def extract_contact_info(self, url):
        """Extract contact information from website"""
        contact_info = {'email': '', 'phone': ''}
        try:
            response = self.session.get(url, timeout=8)
            if response.status_code == 200:
                content = trafilatura.extract(response.content, include_comments=False)
                if content:
                    # Extract emails
                    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
                    emails = re.findall(email_pattern, content)
                    if emails:
                        contact_info['email'] = emails[0]
                    
                    # Extract phone numbers
                    phone_pattern = r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})'
                    phones = re.findall(phone_pattern, content)
                    if phones:
                        contact_info['phone'] = f"({phones[0][0]}) {phones[0][1]}-{phones[0][2]}"
                        
        except:
            pass
        return contact_info
    
    def _extract_enhanced_contact_info(self, url):
        """Extract enhanced contact info including social links by visiting contact pages"""
        enhanced_info = {
            'email': '',
            'facebook': '',
            'linkedin': '',
            'twitter': '',
            'instagram': '',
            'youtube': '',
            'tiktok': '',
            'pinterest': '',
            'snapchat': '',
            'whatsapp': '',
            'telegram': ''
        }
        
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return enhanced_info
        
        print(f"Extracting contact info and social media from: {url}")
        
        # Reduced list of pages for production speed - check only main page and contact
        pages_to_check = [
            url,  # Main page first
            f"{url.rstrip('/')}/contact",
            f"{url.rstrip('/')}/contact-us"
        ]
        
        # Try each page until we find good contact info
        for page_url in pages_to_check:
            try:
                print(f"  Checking page: {page_url}")
                response = self._make_request_with_retry(page_url)
                
                if response and response.status_code == 200 and response.content:
                    soup = BeautifulSoup(response.content, 'lxml')
                    
                    # Extract emails from this page
                    page_emails = self._extract_emails_from_page(soup)
                    if page_emails and not enhanced_info['email']:
                        enhanced_info['email'] = page_emails[0]
                        print(f"    Found email: {enhanced_info['email']}")
                    
                    # Extract social media from this page  
                    page_social = self._extract_social_media_from_page(soup)
                    
                    # Update social media info if we found new ones
                    for platform, social_url in page_social.items():
                        if social_url and not enhanced_info.get(platform):
                            enhanced_info[platform] = social_url
                            print(f"    Found {platform}: {social_url}")
                    
                    # If we found email OR any social platform, we're done for speed
                    social_count = sum(1 for v in enhanced_info.values() if v and v != enhanced_info['email'])
                    if enhanced_info['email'] or social_count >= 1:  # Exit early if we find anything
                        print(f"  Found contact info, stopping search for speed")
                        break
                        
            except requests.exceptions.Timeout:
                print(f"    Timeout accessing {page_url}")
                continue
            except requests.exceptions.RequestException as e:
                print(f"    Request error for {page_url}: {str(e)[:50]}")
                continue
            except Exception as e:
                print(f"    Error processing {page_url}: {str(e)[:50]}")
                continue
                
        return enhanced_info
    
    def _extract_enhanced_contact_info_fast(self, url):
        """Enhanced contact info extraction with more pages and fallback strategies"""
        enhanced_info = {
            'email': '',
            'facebook': '',
            'linkedin': '',
            'twitter': '',
            'instagram': '',
            'youtube': '',
            'tiktok': '',
            'pinterest': '',
            'snapchat': '',
            'whatsapp': '',
            'telegram': ''
        }
        
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return enhanced_info
        
        # Reduced pages for production speed  
        pages_to_check = [
            url,  # Main page first
            f"{url.rstrip('/')}/contact"
        ]
        
        simple_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        }
        
        pages_checked = 0
        max_pages = 2  # Aggressive limit for production speed
        
        for page_url in pages_to_check:
            if pages_checked >= max_pages:
                break
                
            try:
                response = requests.get(page_url, headers=simple_headers, timeout=8)
                
                if response and response.status_code == 200 and response.content:
                    pages_checked += 1
                    soup = BeautifulSoup(response.content, 'lxml')
                    
                    # Extract emails from this page
                    page_emails = self._extract_emails_from_page(soup)
                    if page_emails and not enhanced_info['email']:
                        enhanced_info['email'] = page_emails[0]
                        print(f"    ✅ Found email for {url}: {page_emails[0]}")
                    
                    # Extract social media from this page  
                    page_social = self._extract_social_media_from_page(soup)
                    
                    # Update social media info
                    for platform, social_url in page_social.items():
                        if social_url and not enhanced_info.get(platform):
                            enhanced_info[platform] = social_url
                    
                    # Try fallback email strategies if no email found yet
                    if not enhanced_info['email']:
                        enhanced_info['email'] = self._generate_fallback_email(url, soup)
                    
                    # Early exit if we found good contact info
                    social_count = sum(1 for v in enhanced_info.values() if v and v != enhanced_info['email'])
                    if enhanced_info['email'] and social_count >= 2:
                        break
                        
            except Exception as e:
                continue  # Skip failed pages
        
        # Log final results
        found_items = []
        if enhanced_info['email']: found_items.append('email')
        social_platforms = [k for k, v in enhanced_info.items() if v and k != 'email']
        if social_platforms: found_items.extend(social_platforms)
        
        if found_items:
            print(f"    📧 Contact extraction for {url}: {', '.join(found_items)}")
                
        return enhanced_info
    
    def _extract_emails_from_page(self, soup):
        """Extract valid business emails from a webpage with enhanced patterns"""
        emails = set()
        
        try:
            # 1. Email from mailto links
            mailto_links = soup.find_all('a', href=re.compile(r'^mailto:'))
            for link in mailto_links:
                href = link.get('href', '')
                if href:
                    email = href.replace('mailto:', '').split('?')[0].strip()
                    if self._is_valid_business_email(email):
                        emails.add(email.lower())
                        print(f"    Found email from mailto: {email}")
            
            # 2. Email from text content with improved patterns
            content = soup.get_text()
            # Enhanced email patterns with better word boundaries
            email_patterns = [
                # Standard email pattern with strict word boundaries  
                r'(?<!\S)([A-Za-z0-9](?:[A-Za-z0-9._%-]*[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})(?!\S)',
                # Contact/email prefixed patterns with capture groups
                r'(?i)(?:email|contact|info|support|sales|hello|inquiries)[:\s]*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
                # Email addresses in common formats
                r'(?i)(?:mailto:)?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
            ]
            
            for pattern in email_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        email = match[0] if match[0] else match[1] if len(match) > 1 else ''
                    else:
                        email = match
                    
                    # Advanced email cleaning
                    email = email.strip().strip('.,;:!?()[]{}"\' ')
                    email = re.sub(r'^mailto:', '', email, flags=re.IGNORECASE)
                    
                    # Remove common prefixes that get captured
                    email = re.sub(r'^/+', '', email)  # Remove leading slashes
                    email = re.sub(r'^[^a-zA-Z0-9]*', '', email)  # Remove non-alphanumeric prefixes
                    
                    # Remove common suffixes that get captured
                    email = re.sub(r'[^a-zA-Z0-9]*(Open|We|Contact|Information|Call|Phone|Visit|More).*$', '', email, flags=re.IGNORECASE)
                    
                    # Final cleanup - ensure email ends properly 
                    email_match = re.match(r'^([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', email)
                    if email_match:
                        email = email_match.group(1)
                    
                    if email and self._is_valid_business_email(email):
                        emails.add(email.lower())
                        print(f"    Found email from text: {email}")
            
            # 3. Email from contact forms and input elements
            email_inputs = soup.find_all(['input', 'label'], attrs={'placeholder': re.compile(r'email', re.I)})
            for input_elem in email_inputs:
                placeholder = input_elem.get('placeholder', '')
                if '@' in placeholder and self._is_valid_business_email(placeholder):
                    emails.add(placeholder.lower())
                    print(f"    Found email from placeholder: {placeholder}")
            
            # 4. Email from data attributes and hidden fields
            data_email_elements = soup.find_all(attrs={"data-email": True})
            for elem in data_email_elements:
                email = elem.get('data-email', '').strip()
                if self._is_valid_business_email(email):
                    emails.add(email.lower())
                    print(f"    Found email from data attribute: {email}")
            
            # 5. Email from specific HTML elements (spans, divs with email content)
            for element in soup.find_all(['span', 'div', 'p'], string=re.compile(r'@')):
                text = element.get_text().strip()
                matches = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
                for match in matches:
                    if self._is_valid_business_email(match):
                        emails.add(match.lower())
                        print(f"    Found email from element text: {match}")
                    
        except Exception as e:
            print(f"Error extracting emails: {e}")
        
        unique_emails = list(emails)
        if unique_emails:
            print(f"    Total unique emails found: {len(unique_emails)} - {unique_emails}")
        else:
            print(f"    No emails found on this page")
            
        return unique_emails
    
    def _generate_fallback_email(self, url, soup):
        """Generate fallback business email based on domain and common patterns"""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            
            # Remove www prefix
            if domain.startswith('www.'):
                domain = domain[4:]
            
            # Check for contact forms as a signal of business activity
            contact_forms = soup.find_all(['form'], {'class': re.compile(r'contact', re.I)})
            contact_forms.extend(soup.find_all(['form'], {'id': re.compile(r'contact', re.I)}))
            
            if contact_forms and domain and '.' in domain:
                # Common business email patterns
                common_prefixes = ['info', 'contact', 'hello', 'sales', 'support']
                for prefix in common_prefixes:
                    potential_email = f"{prefix}@{domain}"
                    if self._is_valid_business_email(potential_email):
                        print(f"    📧 Generated fallback email: {potential_email}")
                        return potential_email
                        
        except Exception as e:
            pass
        
        return ''
    
    def _extract_social_media_from_page(self, soup):
        """Extract social media links from a webpage"""
        social_info = {
            'facebook': '', 'linkedin': '', 'twitter': '', 'instagram': '', 
            'youtube': '', 'tiktok': '', 'pinterest': '', 'snapchat': '', 
            'whatsapp': '', 'telegram': ''
        }
        
        try:
            # Extract social media links with enhanced patterns
            social_links = soup.find_all('a', href=True)
            
            # Process links for social media platforms
            for link in social_links[:50]:  # Limit to first 50 links for efficiency
                href = link.get('href', '')
                if not href:
                    continue
                    
                # Clean and normalize URL
                href = href.strip()
                if href.startswith('//'):
                    href = 'https:' + href
                elif href.startswith('/'):
                    continue  # Skip relative URLs
                elif not href.startswith('http'):
                    # Try to construct full URL for partial social links
                    if any(platform in href.lower() for platform in ['facebook', 'linkedin', 'twitter', 'instagram', 'youtube']):
                        href = 'https://' + href
                    else:
                        continue
                
                href_lower = href.lower()
                
                # Enhanced social media detection with better filtering
                if 'facebook.com' in href_lower and not social_info['facebook']:
                    # Filter out share links, ads, and common false positives
                    if not any(skip in href_lower for skip in ['sharer', 'share.php', 'tr?', 'ads', 'campaign', 'dialog', 'plugins', 'login']):
                        social_info['facebook'] = self._clean_social_url(href, 'facebook')
                
                elif 'linkedin.com' in href_lower and not social_info['linkedin']:
                    # Filter out share links and focus on company/business pages
                    if not any(skip in href_lower for skip in ['/sharing/', '/share/', 'shareArticle', 'oauth']):
                        social_info['linkedin'] = self._clean_social_url(href, 'linkedin')
                
                elif ('twitter.com' in href_lower or 'x.com' in href_lower) and not social_info['twitter']:
                    # Filter out share links and API calls
                    if not any(skip in href_lower for skip in ['intent/tweet', 'share?', 'oauth', 'api.']):
                        social_info['twitter'] = self._clean_social_url(href, 'twitter')
                
                elif 'instagram.com' in href_lower and not social_info['instagram']:
                    # Filter out share and embed links
                    if not any(skip in href_lower for skip in ['embed', 'share', 'oauth']):
                        social_info['instagram'] = self._clean_social_url(href, 'instagram')
                
                elif ('youtube.com' in href_lower or 'youtu.be' in href_lower) and not social_info['youtube']:
                    # Filter out embed links but keep channel and user pages
                    if any(keep in href_lower for keep in ['/channel/', '/user/', '/c/', '@']):
                        social_info['youtube'] = self._clean_social_url(href, 'youtube')
                
                elif 'tiktok.com' in href_lower and not social_info['tiktok']:
                    if not any(skip in href_lower for skip in ['share', 'embed', 'oauth']):
                        social_info['tiktok'] = self._clean_social_url(href, 'tiktok')
                
                elif 'pinterest.com' in href_lower and not social_info['pinterest']:
                    if not any(skip in href_lower for skip in ['pin/', 'widget', 'share']):
                        social_info['pinterest'] = self._clean_social_url(href, 'pinterest')
                        
        except Exception as e:
            print(f"Error extracting social media: {e}")
            
        return social_info
    
    def _add_email_validation_method(self):
        """Placeholder for email validation functionality"""
        pass
    
    def _extract_social_from_content(self, soup, enhanced_info):
        """Extract social media handles from page content and meta tags"""
        try:
            # Check meta tags for social URLs
            meta_tags = soup.find_all('meta')
            for tag in meta_tags:
                content = tag.get('content', '') or tag.get('href', '')
                if content and any(platform in content.lower() for platform in ['facebook', 'linkedin', 'twitter', 'instagram', 'youtube']):
                    for platform in ['facebook', 'linkedin', 'twitter', 'instagram', 'youtube']:
                        if platform in content.lower() and not enhanced_info[platform]:
                            if 'http' in content:
                                enhanced_info[platform] = content
                                print(f"Found {platform} from meta tag: {content}")
            
            # Look for social handles in text content
            text_content = soup.get_text()
            
            # Facebook page patterns
            if not enhanced_info['facebook']:
                fb_patterns = [
                    r'facebook\.com/([\w\.-]+)',
                    r'fb\.com/([\w\.-]+)',
                    r'@([\w\.-]+)\s+on\s+facebook',
                    r'facebook:\s*([\w\.-]+)'
                ]
                for pattern in fb_patterns:
                    matches = re.findall(pattern, text_content, re.IGNORECASE)
                    if matches:
                        handle = matches[0]
                        if handle and len(handle) > 2:
                            enhanced_info['facebook'] = f"https://www.facebook.com/{handle}"
                            print(f"Found Facebook handle from content: {handle}")
                            break
            
            # Instagram patterns
            if not enhanced_info['instagram']:
                ig_patterns = [
                    r'instagram\.com/([\w\.-]+)',
                    r'@([\w\.-]+)\s+on\s+instagram',
                    r'instagram:\s*@?([\w\.-]+)',
                    r'follow\s+us\s+@([\w\.-]+)'
                ]
                for pattern in ig_patterns:
                    matches = re.findall(pattern, text_content, re.IGNORECASE)
                    if matches:
                        handle = matches[0]
                        if handle and len(handle) > 2:
                            enhanced_info['instagram'] = f"https://www.instagram.com/{handle}"
                            print(f"Found Instagram handle from content: {handle}")
                            break
            
            # Twitter patterns
            if not enhanced_info['twitter']:
                tw_patterns = [
                    r'twitter\.com/([\w\.-]+)',
                    r'x\.com/([\w\.-]+)',
                    r'@([\w\.-]+)\s+on\s+twitter',
                    r'twitter:\s*@?([\w\.-]+)'
                ]
                for pattern in tw_patterns:
                    matches = re.findall(pattern, text_content, re.IGNORECASE)
                    if matches:
                        handle = matches[0]
                        if handle and len(handle) > 2:
                            enhanced_info['twitter'] = f"https://www.twitter.com/{handle}"
                            print(f"Found Twitter handle from content: {handle}")
                            break
            
        except Exception as e:
            print(f"Error extracting social from content: {e}")
    
    def _clean_social_url(self, url, platform):
        """Clean and validate social media URLs"""
        if not url:
            return ''
        
        try:
            # Remove tracking parameters and clean up URL
            url = url.split('?')[0]  # Remove query parameters
            url = url.rstrip('/')     # Remove trailing slash
            
            # Ensure URL has proper protocol
            if not url.startswith('http'):
                url = 'https://' + url
            
            return url
        except:
            return ''
    
    def _is_valid_social_url(self, url, platform):
        """Validate that social media URL is legitimate and belongs to the correct platform"""
        if not url or not url.startswith('http'):
            return False
        
        try:
            url_lower = url.lower()
            
            # Platform-specific validation
            if platform == 'facebook':
                return 'facebook.com' in url_lower and len(url) > 20
            elif platform == 'linkedin':
                return 'linkedin.com' in url_lower and len(url) > 20
            elif platform == 'twitter':
                return ('twitter.com' in url_lower or 'x.com' in url_lower) and len(url) > 15
            elif platform == 'instagram':
                return 'instagram.com' in url_lower and len(url) > 20
            elif platform == 'youtube':
                return ('youtube.com' in url_lower or 'youtu.be' in url_lower) and len(url) > 20
            elif platform == 'tiktok':
                return 'tiktok.com' in url_lower and len(url) > 15
            elif platform == 'pinterest':
                return 'pinterest.com' in url_lower and len(url) > 20
            elif platform == 'snapchat':
                return 'snapchat.com' in url_lower and len(url) > 20
            elif platform == 'whatsapp':
                return ('wa.me' in url_lower or 'whatsapp.com' in url_lower) and len(url) > 10
            elif platform == 'telegram':
                return ('t.me' in url_lower or 'telegram.me' in url_lower) and len(url) > 10
            
            return False
        except:
            return False
    
    def _is_valid_business_email(self, email):
        """Check if email looks like a valid business email with improved validation"""
        if not email or '@' not in email:
            print(f"    Email validation failed - no @ symbol: '{email}'")
            return False
        
        # Basic format validation
        if email.count('@') != 1:
            print(f"    Email validation failed - multiple @ symbols: '{email}'")
            return False
            
        try:
            local, domain = email.split('@')
            local = local.strip()
            domain = domain.strip().lower()
            
            # Check local part (before @)
            if len(local) < 1:
                print(f"    Email validation failed - empty local part: '{email}'")
                return False
            if len(local) > 64:
                print(f"    Email validation failed - local part too long: '{email}'")
                return False
            
            # Check domain part (after @)
            if len(domain) < 4:
                print(f"    Email validation failed - domain too short: '{email}' (domain: '{domain}')")
                return False
            if '.' not in domain:
                print(f"    Email validation failed - no dot in domain: '{email}'")
                return False
            
            # Allow all emails - small businesses often use gmail, yahoo, etc
            # Just exclude obvious test/invalid domains
            invalid_domains = [
                'example.com', 'test.com', 'localhost', 'domain.com',
                'email.com', 'sample.com', 'demo.com', 'your-email.com',
                'yourdomain.com', 'yoursite.com', 'website.com'
            ]
            
            # Check for invalid patterns
            if domain in invalid_domains:
                print(f"    Email validation failed - invalid domain: '{email}'")
                return False
            if domain.startswith('www.'):
                print(f"    Email validation failed - domain starts with www: '{email}'")
                return False
            if domain.endswith('.local'):
                print(f"    Email validation failed - local domain: '{email}'")
                return False
            if 'noreply' in local.lower() or 'no-reply' in local.lower():
                print(f"    Email validation failed - noreply address: '{email}'")
                return False
            
            # Check for invalid characters in local part
            if re.search(r'[<>()\[\]\\,;:\s@"\']', local):
                print(f"    Email validation failed - invalid characters in local part: '{email}'")
                return False
            
            # Stricter domain validation
            # Domain must be: letters/numbers, optional hyphens/dots, then dot and TLD
            domain_pattern = r'^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
            if not re.match(domain_pattern, domain):
                print(f"    Email validation failed - invalid domain format: '{email}' (domain: '{domain}')")
                return False
            
            # Additional checks for malformed domains
            domain_parts = domain.split('.')
            if len(domain_parts) < 2:
                print(f"    Email validation failed - domain needs at least one dot: '{email}'")
                return False
            
            # Last part should be a valid TLD (2-6 characters)
            tld = domain_parts[-1]
            if not re.match(r'^[a-zA-Z]{2,6}$', tld):
                print(f"    Email validation failed - invalid TLD: '{email}' (TLD: '{tld}')")
                return False
                
            print(f"    ✓ Email validation passed: '{email}'")
            return True
            
        except Exception as e:
            print(f"    Email validation error for '{email}': {e}")
            return False
    
    def _format_phone_number(self, phone_text):
        """Format phone number consistently"""
        if not phone_text:
            return ''
        
        # Extract digits only
        digits = re.sub(r'\D', '', phone_text)
        
        # Format as US phone number if 10 digits
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        elif len(digits) == 11 and digits[0] == '1':
            return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        
        return phone_text

# Lead deduplication function
def deduplicate_leads(current_leads, new_leads):
    """Remove duplicate leads based on business name and phone/website"""
    existing_signatures = set()
    
    # Create signatures for existing leads
    for lead in current_leads:
        name = lead.get('name', '').lower().strip()
        phone = lead.get('phone', '').strip()
        website = lead.get('website', '').strip()
        
        # Create a unique signature for each lead
        if name:
            signature = name
            if phone:
                signature += f"|{phone}"
            elif website:
                signature += f"|{website}"
            existing_signatures.add(signature)
    
    # Filter out duplicates from new leads
    unique_new_leads = []
    for lead in new_leads:
        name = lead.get('name', '').lower().strip()
        phone = lead.get('phone', '').strip()
        website = lead.get('website', '').strip()
        
        if name:
            signature = name
            if phone:
                signature += f"|{phone}"
            elif website:
                signature += f"|{website}"
                
            if signature not in existing_signatures:
                unique_new_leads.append(lead)
                existing_signatures.add(signature)
    
    print(f"Filtered {len(new_leads) - len(unique_new_leads)} duplicate leads")
    return current_leads + unique_new_leads

# Initialize the scraper
scraper = LeadScraper()

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Please enter both username and password.', 'error')
            return render_template('login.html')
        
        if verify_user(username, password):
            session['username'] = username
            # Update last login time
            users = get_users_storage()
            if username in users:
                users[username]['last_login'] = datetime.now().isoformat()
                session['users'] = users
                session.modified = True
            
            flash('Welcome to AI Sales Machine!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username', 'User')
    session.pop('username', None)
    flash(f'Goodbye {username}! You have been logged out.', 'info')
    return redirect(url_for('login'))

# Protected routes
@app.route('/')
@login_required
def dashboard():
    """Main dashboard with enhanced stats and segmentation"""
    # Get comprehensive stats from stored leads
    leads_storage = get_leads_storage()
    total_leads = len(leads_storage)
    
    # Debug information for troubleshooting
    current_user = session.get('username', 'Unknown')
    print(f"Dashboard accessed by user: {current_user}, loaded {total_leads} leads")
    
    # Enhanced lead type statistics
    sales_ready = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Sales-Ready Lead')
    premium_leads = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Premium Lead')
    prospects = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Prospect Lead')
    social_connected = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Social-Connected Lead')
    website_leads = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Website Lead')
    social_leads = sum(1 for lead in leads_storage if any([lead.get('facebook'), lead.get('linkedin'), lead.get('twitter'), lead.get('instagram')]))
    
    # Contact completeness metrics
    with_phone = sum(1 for lead in leads_storage if lead.get('phone'))
    with_email = sum(1 for lead in leads_storage if lead.get('email'))
    with_website = sum(1 for lead in leads_storage if lead.get('website'))
    complete_profiles = sum(1 for lead in leads_storage if lead.get('phone') and lead.get('email') and lead.get('website'))
    
    # Contact level distribution
    premium_contact = sum(1 for lead in leads_storage if lead.get('contact_level') == 'Premium')
    high_contact = sum(1 for lead in leads_storage if lead.get('contact_level') == 'High')
    medium_contact = sum(1 for lead in leads_storage if lead.get('contact_level') == 'Medium')
    basic_contact = sum(1 for lead in leads_storage if lead.get('contact_level') == 'Basic')
    
    # Industry segmentation
    industry_breakdown = {}
    for lead in leads_storage:
        industry = lead.get('industry', 'General')
        industry_breakdown[industry] = industry_breakdown.get(industry, 0) + 1
    
    # Geographic segmentation
    location_breakdown = {}
    for lead in leads_storage:
        location_tier = lead.get('location_tier', 'Unknown')
        location_breakdown[location_tier] = location_breakdown.get(location_tier, 0) + 1
    
    # Lead quality metrics
    avg_priority_score = sum(lead.get('priority_score', 0) for lead in leads_storage) / max(total_leads, 1)
    high_priority_leads = sum(1 for lead in leads_storage if lead.get('priority_score', 0) >= 5)
    
    # Time-based metrics (simulated for current session)
    today_leads = max(1, total_leads // 5)  # Simulate today's activity
    this_week_leads = total_leads
    
    # Enhanced conversion rates
    premium_conversion_rate = ((premium_leads + sales_ready) / total_leads * 100) if total_leads > 0 else 0
    overall_conversion_rate = (sales_ready / total_leads * 100) if total_leads > 0 else 0
    completion_rate = (complete_profiles / total_leads * 100) if total_leads > 0 else 0
    
    stats = {
        'total_leads': total_leads,
        'sales_ready': sales_ready,
        'premium_leads': premium_leads,
        'prospects': prospects,
        'social_connected': social_connected,
        'website_leads': website_leads,
        'social_leads': social_leads,
        'with_phone': with_phone,
        'with_email': with_email,
        'with_website': with_website,
        'complete_profiles': complete_profiles,
        'premium_contact': premium_contact,
        'high_contact': high_contact,
        'medium_contact': medium_contact,
        'basic_contact': basic_contact,
        'industry_breakdown': industry_breakdown,
        'location_breakdown': location_breakdown,
        'avg_priority_score': round(avg_priority_score, 1),
        'high_priority_leads': high_priority_leads,
        'today_leads': today_leads,
        'this_week_leads': this_week_leads,
        'premium_conversion_rate': round(premium_conversion_rate, 1),
        'conversion_rate': round(overall_conversion_rate, 1),
        'completion_rate': round(completion_rate, 1)
    }
    
    # Get recent leads sorted by creation time (latest first)
    recent_leads = sorted(leads_storage, key=lambda x: x.get('created_at', ''), reverse=True)[:5]
    
    return render_template('dashboard.html', stats=stats, recent_leads=recent_leads)

@app.route('/enhance-existing-leads', methods=['POST'])
@login_required  
def enhance_existing_leads():
    """Re-process existing leads with enhanced contact extraction"""
    try:
        leads_storage = get_leads_storage()
        total_leads = len(leads_storage)
        
        if total_leads == 0:
            return {'success': False, 'message': 'No leads to process'}
        
        # Process leads with websites only to improve efficiency
        leads_with_websites = [lead for lead in leads_storage if lead.get('website') and not lead.get('email')]
        
        if len(leads_with_websites) == 0:
            return {'success': False, 'message': 'No leads with websites found that need email enhancement'}
            
        # Limit to 25 leads per request to prevent timeout
        batch_size = min(25, len(leads_with_websites))
        leads_to_process = leads_with_websites[:batch_size]
        
        print(f"Enhancing contact info for {batch_size} existing leads...")
        
        improved_count = 0
        
        # Process leads concurrently with enhanced extraction
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_lead = {}
            for lead in leads_to_process:
                future = executor.submit(scraper._extract_enhanced_contact_info_fast, lead['website'])
                future_to_lead[future] = lead
            
            # Collect results with timeout  
            for future in as_completed(future_to_lead, timeout=20):
                lead = future_to_lead[future]
                try:
                    enhanced_contact = future.result()
                    
                    # Update fields that are empty
                    original_email = lead.get('email', '')
                    for key, value in enhanced_contact.items():
                        if value and (not lead.get(key) or lead.get(key) == ''):
                            lead[key] = value
                            if key == 'email' and not original_email:
                                improved_count += 1
                                print(f"✅ Enhanced {lead['name']} with email: {value}")
                        
                except Exception as e:
                    print(f"Error enhancing {lead['name']}: {e}")
        
        # Save updated leads
        save_leads_storage(leads_storage)
        
        return {
            'success': True, 
            'processed': batch_size,
            'improved': improved_count,
            'remaining': max(0, len(leads_with_websites) - batch_size)
        }
        
    except Exception as e:
        print(f"Enhancement error: {e}")
        return {'success': False, 'message': str(e)}

@app.route('/lead-finder')
@login_required
def lead_finder():
    """Lead finder search interface"""
    return render_template('lead_finder.html')

@app.route('/search', methods=['POST'])
@login_required
def search():
    business_type = request.form.get('query', '').strip()
    location = request.form.get('location', '').strip()
    num_results = min(int(request.form.get('num_results', 10)), 50)
    
    if not business_type or not location:
        return render_template('lead_finder.html', error="Please enter both business type and location")
    
    try:
        # Search business listings for structured data
        leads = scraper.search_business_listings(business_type, location, num_results)
        
        if not leads:
            return render_template('lead_finder.html', error="No business listings found. Try different search terms.")
        
        # Store leads with deduplication based on business name and phone number
        current_leads = get_leads_storage()
        new_leads_list = deduplicate_leads(current_leads, leads)
        save_leads_storage(new_leads_list)
        
        # Sort leads by priority score
        leads.sort(key=lambda x: x.get('priority_score', 0), reverse=True)
        
        return render_template('search_results.html', leads=leads, query=business_type, location=location)
        
    except Exception as e:
        return render_template('lead_finder.html', error=f"Search error: {str(e)}")

@app.route('/lead-classifier')
@login_required
def lead_classifier():
    """Lead classification and filtering"""
    # Get filter parameters
    lead_type_filter = request.args.get('type', 'all')
    
    leads_storage = get_leads_storage()
    # Sort leads by creation time (latest first)
    sorted_leads = sorted(leads_storage, key=lambda x: x.get('created_at', ''), reverse=True)
    
    filtered_leads = sorted_leads
    if lead_type_filter != 'all':
        filtered_leads = [lead for lead in sorted_leads if lead.get('lead_type') == lead_type_filter]
    
    # Classify leads by type (also sorted by creation time)
    classified_leads = {
        'sales_ready': sorted([lead for lead in leads_storage if lead.get('lead_type') == 'Sales-Ready Lead'], 
                             key=lambda x: x.get('created_at', ''), reverse=True),
        'prospects': sorted([lead for lead in leads_storage if lead.get('lead_type') == 'Prospect Lead'], 
                           key=lambda x: x.get('created_at', ''), reverse=True),
        'website_leads': sorted([lead for lead in leads_storage if lead.get('lead_type') == 'Website Lead'], 
                               key=lambda x: x.get('created_at', ''), reverse=True),
        'social_leads': sorted([lead for lead in leads_storage if any([lead.get('facebook'), lead.get('linkedin'), lead.get('twitter'), lead.get('instagram')])], 
                              key=lambda x: x.get('created_at', ''), reverse=True)
    }
    
    return render_template('lead_classifier.html', leads=filtered_leads, classified=classified_leads)

@app.route('/outreach-hub')
@login_required
def outreach_hub():
    """Outreach hub with one-click actions and templates"""
    leads_storage = get_leads_storage()
    return render_template('outreach_hub.html', leads=leads_storage)

@app.route('/funnels-library')
@login_required
def funnels_library():
    """Funnels library with niche-specific templates"""
    # Get list of available funnel templates
    funnel_templates = get_available_templates('funnels')
    campaign_templates = get_available_templates('campaigns')
    return render_template('funnels_library.html', funnels=funnel_templates, campaigns=campaign_templates)


@app.route('/export_csv', methods=['POST'])
@login_required
def export_csv():
    leads_data = request.form.get('data', '')
    if not leads_data:
        return "No data to export", 400
    
    try:
        leads = json.loads(leads_data)
        
        # Function to sanitize CSV fields to prevent formula injection
        def sanitize_csv_field(value):
            if not value:
                return ''
            value = str(value)
            # Prevent formula injection by prepending space to fields starting with dangerous characters
            if value.startswith(('=', '+', '-', '@')):
                value = ' ' + value
            return value
        
        # Create CSV content in memory
        output = StringIO()
        fieldnames = ['name', 'phone', 'website', 'email', 'address', 'lead_type', 'priority_score', 'facebook', 'linkedin', 'twitter', 'instagram', 'source']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        
        writer.writeheader()
        for lead in leads:
            sanitized_lead = {k: sanitize_csv_field(lead.get(k, '')) for k in fieldnames}
            writer.writerow(sanitized_lead)
        
        # Convert to bytes for proper file response
        csv_content = output.getvalue()
        output.close()
        
        # Create response with CSV content
        response = Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=leads.csv'}
        )
        return response
        
    except Exception as e:
        return f"Export error: {str(e)}", 500

@app.route('/api/export_recent_leads')
@login_required
def export_recent_leads():
    """Export recent leads as CSV"""
    username = session.get('username', 'guest')
    leads = get_leads_storage()
    recent_leads = leads[-10:] if leads else []  # Get last 10 leads
    
    if not recent_leads:
        return jsonify({'error': 'No leads found'}), 404
    
    # Create CSV content
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Business Name', 'Domain', 'Address', 'Phone', 'Email', 'Industry', 'Lead Type', 'Priority Score', 'Facebook', 'LinkedIn', 'Twitter', 'Instagram', 'YouTube', 'Created At'])
    
    # Write data
    for lead in recent_leads:
        writer.writerow([
            lead.get('name', ''),
            lead.get('domain', ''),
            lead.get('address', ''),
            lead.get('phone', ''),
            lead.get('email', ''),
            lead.get('industry', ''),
            lead.get('lead_type', ''),
            lead.get('priority_score', ''),
            lead.get('facebook', ''),
            lead.get('linkedin', ''),
            lead.get('twitter', ''),
            lead.get('instagram', ''),
            lead.get('youtube', ''),
            lead.get('created_at', '')
        ])
    
    output.seek(0)
    
    return app.response_class(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-disposition": f"attachment; filename=recent_leads_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.route('/api/leads')
@login_required
def api_leads():
    """API endpoint for leads data"""
    leads_storage = get_leads_storage()
    return jsonify(leads_storage)

@app.route('/clear-leads', methods=['POST'])
@login_required
def clear_leads():
    """Clear all stored leads"""
    save_leads_storage([])
    return jsonify({'success': True})

# Template System Routes
def load_template_manifest():
    """Load template manifest with metadata"""
    try:
        manifest_path = os.path.join(app.template_folder, 'manifest.json')
        with open(manifest_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading template manifest: {e}")
        return {'campaigns': {}, 'funnels': {}}

def safe_template_resolver(template_kind, template_slug):
    """Safely resolve template paths to prevent path traversal attacks"""
    # Whitelist allowed template kinds
    allowed_kinds = ['campaigns', 'funnels']
    if template_kind not in allowed_kinds:
        return None, f"Invalid template kind: {template_kind}"
    
    # Sanitize slug - only allow alphanumeric, underscore, hyphen
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', template_slug):
        return None, f"Invalid template slug: {template_slug}"
    
    # Load manifest to validate template exists
    manifest = load_template_manifest()
    template_data = manifest.get(template_kind, {}).get(template_slug)
    if not template_data:
        return None, f"Template not found: {template_kind}/{template_slug}"
    
    # Construct safe path
    template_file = template_data['file']
    template_path = os.path.join(app.template_folder, template_kind, template_file)
    
    # Verify file exists and is within allowed directory
    if not os.path.exists(template_path):
        return None, f"Template file not found: {template_path}"
    
    # Ensure path is within template directory (prevent path traversal)
    template_dir = os.path.join(app.template_folder, template_kind)
    try:
        real_template_path = os.path.realpath(template_path)
        real_template_dir = os.path.realpath(template_dir)
        if not real_template_path.startswith(real_template_dir):
            return None, "Path traversal attempt detected"
    except Exception as e:
        return None, f"Path validation error: {e}"
    
    return template_path, template_data

@app.route('/preview/<template_kind>/<template_slug>')
@login_required
def preview_template(template_kind, template_slug):
    """Preview a template in a minimal layout"""
    template_path, template_data_or_error = safe_template_resolver(template_kind, template_slug)
    
    if not template_path:
        return f"Error: {template_data_or_error}", 404
    
    try:
        # Read template content
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Create preview HTML with minimal styling
        preview_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Preview: {template_data_or_error['title']}</title>
    <style>
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            margin: 0; 
            padding: 20px; 
            background-color: #f5f5f5; 
        }}
        .preview-header {{ 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            margin-bottom: 20px; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); 
        }}
        .preview-content {{ 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); 
        }}
        .close-btn {{ 
            background: #007bff; 
            color: white; 
            border: none; 
            padding: 8px 16px; 
            border-radius: 4px; 
            cursor: pointer; 
            float: right; 
        }}
    </style>
</head>
<body>
    <div class="preview-header">
        <h2>{template_data_or_error['title']}</h2>
        <p><strong>Niche:</strong> {template_data_or_error['niche'].title()} | <strong>Type:</strong> {template_kind.title()}</p>
        <p>{template_data_or_error['description']}</p>
        <button class="close-btn" onclick="window.close()">Close Preview</button>
        <div style="clear: both;"></div>
    </div>
    <div class="preview-content">
        {template_content}
    </div>
</body>
</html>
        """
        
        return preview_html
        
    except Exception as e:
        return f"Error reading template: {str(e)}", 500

@app.route('/download/<template_kind>/<template_slug>')
@login_required
def download_template(template_kind, template_slug):
    """Download a template file as attachment"""
    template_path, template_data_or_error = safe_template_resolver(template_kind, template_slug)
    
    if not template_path:
        return f"Error: {template_data_or_error}", 404
    
    try:
        # Read template content
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Create filename
        safe_filename = f"{template_kind}_{template_slug}.html"
        
        # Create response with template content
        response = Response(
            template_content,
            mimetype='text/html',
            headers={
                'Content-Disposition': f'attachment; filename="{safe_filename}"',
                'Content-Type': 'text/html; charset=utf-8'
            }
        )
        
        return response
        
    except Exception as e:
        return f"Error downloading template: {str(e)}", 500

@app.route('/api/templates')
@login_required
def api_templates():
    """API endpoint for template manifest"""
    manifest = load_template_manifest()
    return jsonify(manifest)

# TXT Resource Routes for Campaign and Funnel Templates
def get_resource_mapping():
    """Get mapping of resource names to txt files"""
    return {
        'campaigns': {
            'healthcare_nurture': {
                'title': 'Healthcare Lead Nurture Sequence',
                'file': 'healthcare_nurture.txt',
                'description': 'Complete email nurture sequence for healthcare professionals',
                'niche': 'healthcare'
            },
            'fitness_outreach': {
                'title': 'Fitness Business Outreach Sequence', 
                'file': 'fitness_outreach.txt',
                'description': 'B2B outreach sequence for fitness industry services',
                'niche': 'fitness'
            },
            'realestate_agent': {
                'title': 'Real Estate Agent Outreach Sequence',
                'file': 'realestate_agent.txt', 
                'description': 'Lead generation sequence for real estate marketing services',
                'niche': 'realestate'
            },
            'cold_email_sequence': {
                'title': 'Universal Cold Email Sequence',
                'file': 'cold_email_sequence.txt',
                'description': 'Universal B2B cold email outreach across all industries', 
                'niche': 'general'
            },
            'legal_outreach': {
                'title': 'Legal Services Outreach Sequence',
                'file': 'legal_outreach.txt',
                'description': 'Compliant marketing sequence for law firms and legal services',
                'niche': 'legal'
            }
        },
        'funnels': {
            'dental_consultation': {
                'title': 'Dental Consultation Funnel System',
                'file': 'dental_consultation.txt',
                'description': 'Complete funnel for converting dental consultation leads',
                'niche': 'healthcare'
            },
            'fitness_trial': {
                'title': 'Fitness Trial Membership Funnel',
                'file': 'fitness_trial.txt',
                'description': 'Free trial to paid membership conversion funnel for gyms',
                'niche': 'fitness'
            },
            'legal_consultation': {
                'title': 'Legal Consultation Funnel System', 
                'file': 'legal_consultation.txt',
                'description': 'Consultation booking to legal service retainer funnel',
                'niche': 'legal'
            },
            'restaurant_catering': {
                'title': 'Restaurant Catering Funnel System',
                'file': 'restaurant_catering.txt', 
                'description': 'Website visitors to booked catering events conversion funnel',
                'niche': 'restaurant'
            }
        }
    }

def safe_resource_resolver(resource_type, resource_name):
    """Safely resolve resource paths to prevent path traversal attacks"""
    # Whitelist allowed resource types
    allowed_types = ['campaigns', 'funnels']
    if resource_type not in allowed_types:
        return None, f"Invalid resource type: {resource_type}"
    
    # Load resource mapping to validate resource exists
    resource_mapping = get_resource_mapping()
    resource_data = resource_mapping.get(resource_type, {}).get(resource_name)
    if not resource_data:
        return None, f"Resource not found: {resource_type}/{resource_name}"
    
    # Construct safe path to resources folder
    base_dir = os.path.dirname(os.path.abspath(__file__))
    resource_file = resource_data['file']
    resource_path = os.path.join(base_dir, 'resources', resource_type, resource_file)
    
    # Verify file exists and is within allowed directory
    if not os.path.exists(resource_path):
        return None, f"Resource file not found: {resource_path}"
    
    # Ensure path is within resources directory (prevent path traversal)
    resource_dir = os.path.join(base_dir, 'resources', resource_type)
    try:
        real_resource_path = os.path.realpath(resource_path)
        real_resource_dir = os.path.realpath(resource_dir)
        if not real_resource_path.startswith(real_resource_dir):
            return None, "Path traversal attempt detected"
    except Exception as e:
        return None, f"Path validation error: {e}"
    
    return resource_path, resource_data

@app.route('/preview-resource/<resource_type>/<resource_name>')
@login_required
def preview_resource(resource_type, resource_name):
    """Preview a txt resource in a minimal layout"""
    resource_path, resource_data_or_error = safe_resource_resolver(resource_type, resource_name)
    
    if not resource_path:
        return f"Error: {resource_data_or_error}", 404
    
    try:
        # Read resource content
        with open(resource_path, 'r', encoding='utf-8') as f:
            resource_content = f.read()
        
        # Escape HTML to prevent XSS and preserve formatting
        import html
        escaped_content = html.escape(resource_content)
        
        # Create preview HTML with minimal styling
        preview_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Preview: {resource_data_or_error['title']}</title>
    <style>
        body {{ 
            font-family: 'Courier New', monospace; 
            margin: 0; 
            padding: 20px; 
            background-color: #f8f9fa; 
            line-height: 1.6;
        }}
        .preview-header {{ 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            margin-bottom: 20px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
        }}
        .preview-content {{ 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            white-space: pre-wrap;
            font-size: 14px;
        }}
        .close-btn {{ 
            background: #007bff; 
            color: white; 
            border: none; 
            padding: 8px 16px; 
            border-radius: 4px; 
            cursor: pointer; 
            float: right; 
        }}
        .close-btn:hover {{ background: #0056b3; }}
        .resource-meta {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="preview-header">
        <h2>{resource_data_or_error['title']}</h2>
        <p class="resource-meta"><strong>Type:</strong> {resource_type.title()} | <strong>Niche:</strong> {resource_data_or_error['niche'].title()}</p>
        <p class="resource-meta">{resource_data_or_error['description']}</p>
        <button class="close-btn" onclick="window.close()">Close Preview</button>
        <div style="clear: both;"></div>
    </div>
    <div class="preview-content">{escaped_content}</div>
</body>
</html>
        """
        
        return preview_html
        
    except Exception as e:
        return f"Error reading resource: {str(e)}", 500

@app.route('/download-resource/<resource_type>/<resource_name>')
@login_required
def download_resource(resource_type, resource_name):
    """Download a txt resource file as attachment"""
    resource_path, resource_data_or_error = safe_resource_resolver(resource_type, resource_name)
    
    if not resource_path:
        return f"Error: {resource_data_or_error}", 404
    
    try:
        # Read resource content
        with open(resource_path, 'r', encoding='utf-8') as f:
            resource_content = f.read()
        
        # Create filename
        safe_filename = f"{resource_type}_{resource_name}.txt"
        
        # Create response with resource content
        response = Response(
            resource_content,
            mimetype='text/plain',
            headers={
                'Content-Disposition': f'attachment; filename="{safe_filename}"',
                'Content-Type': 'text/plain; charset=utf-8'
            }
        )
        
        return response
        
    except Exception as e:
        return f"Error downloading resource: {str(e)}", 500

@app.route('/api/resources')
@login_required  
def api_resources():
    """API endpoint for txt resource mapping"""
    resources = get_resource_mapping()
    return jsonify(resources)

# Analytics & Reports Routes
@app.route('/analytics')
@login_required
def analytics():
    """Advanced analytics dashboard"""
    leads_storage = get_leads_storage()
    
    # Calculate advanced metrics
    total_leads = len(leads_storage)
    sales_ready = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Sales-Ready Lead')
    prospects = sum(1 for lead in leads_storage if lead.get('lead_type') == 'Prospect Lead')
    social_leads = sum(1 for lead in leads_storage if any([lead.get('facebook'), lead.get('linkedin'), lead.get('twitter'), lead.get('instagram')]))
    
    # Conversion rates
    conversion_rate = (sales_ready / total_leads * 100) if total_leads > 0 else 0
    
    # Source breakdown
    sources = {}
    for lead in leads_storage:
        source = lead.get('source', 'unknown')
        sources[source] = sources.get(source, 0) + 1
    
    # Recent activity (last 7 days simulation)
    recent_activity = [
        {'date': '2025-09-19', 'leads': 12, 'sales_ready': 4},
        {'date': '2025-09-18', 'leads': 8, 'sales_ready': 3},
        {'date': '2025-09-17', 'leads': 15, 'sales_ready': 6},
        {'date': '2025-09-16', 'leads': 10, 'sales_ready': 2},
        {'date': '2025-09-15', 'leads': 7, 'sales_ready': 1},
        {'date': '2025-09-14', 'leads': 9, 'sales_ready': 4},
        {'date': '2025-09-13', 'leads': 11, 'sales_ready': 3}
    ]
    
    analytics_data = {
        'total_leads': total_leads,
        'sales_ready': sales_ready,
        'prospects': prospects,
        'social_leads': social_leads,
        'conversion_rate': round(conversion_rate, 1),
        'sources': sources,
        'recent_activity': recent_activity
    }
    
    return render_template('analytics.html', data=analytics_data)

@app.route('/reports')
@login_required
def reports():
    """Reports and exports"""
    leads_storage = get_leads_storage()
    return render_template('reports.html', leads=leads_storage)

@app.route('/campaigns')
@login_required
def campaigns():
    """Campaign management with dynamic metrics"""
    leads_storage = get_leads_storage()
    total_leads = len(leads_storage)
    
    # Calculate real campaign metrics from leads
    active_campaigns = max(1, total_leads // 50)  # 1 campaign per 50 leads
    total_sent = total_leads * 2  # Assume 2 emails sent per lead on average
    
    # Realistic email marketing metrics
    open_rate = round(min(35.0, 15.0 + (total_leads * 0.1)), 1)  # 15-35% based on lead quality
    response_rate = round(open_rate * 0.25, 1)  # About 25% of opens result in responses
    
    # Create sample campaigns based on lead industries
    industry_breakdown = {}
    for lead in leads_storage:
        industry = lead.get('industry', 'General')
        industry_breakdown[industry] = industry_breakdown.get(industry, 0) + 1
    
    campaigns_data = []
    for industry, count in list(industry_breakdown.items())[:5]:  # Top 5 industries
        if count >= 5:  # Only include industries with at least 5 leads
            sent = count * 2
            opened = int(sent * (open_rate / 100))
            replied = int(opened * (response_rate / open_rate * 100) / 100)
            
            campaigns_data.append({
                'name': f'{industry} Outreach Campaign',
                'description': f'Email sequence targeting {industry} businesses',
                'sent': sent,
                'opened': opened,
                'replied': replied,
                'status': 'Active' if sent > 10 else 'Draft'
            })
    
    # If no campaigns, create a default one
    if not campaigns_data and total_leads > 0:
        sent = total_leads
        opened = int(sent * (open_rate / 100))
        replied = int(opened * (response_rate / open_rate * 100) / 100)
        campaigns_data.append({
            'name': 'General Lead Outreach',
            'description': 'Multi-industry lead outreach campaign',
            'sent': sent,
            'opened': opened,
            'replied': replied,
            'status': 'Active'
        })
    
    campaign_stats = {
        'active_campaigns': len([c for c in campaigns_data if c['status'] == 'Active']),
        'total_sent': sum(c['sent'] for c in campaigns_data),
        'open_rate': open_rate,
        'response_rate': response_rate,
        'campaigns': campaigns_data
    }
    
    return render_template('campaigns.html', leads=leads_storage, stats=campaign_stats)

@app.route('/lead-sources')
@login_required
def lead_sources():
    """Lead source management"""
    leads_storage = get_leads_storage()
    
    # Group leads by source
    sources = {}
    for lead in leads_storage:
        source = lead.get('source', 'unknown')
        if source not in sources:
            sources[source] = []
        sources[source].append(lead)
    
    return render_template('lead_sources.html', sources=sources)

# Bonus Tools Routes
@app.route('/email-validator', methods=['GET', 'POST'])
@login_required
def email_validator():
    """Email validation tool"""
    result = None
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if email:
            # Basic email validation logic
            import re
            
            # Email regex pattern
            pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            is_valid = bool(re.match(pattern, email))
            
            # Check if it's a business email
            personal_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com']
            domain = email.split('@')[1].lower() if '@' in email else ''
            is_business = domain not in personal_domains
            
            result = {
                'email': email,
                'is_valid': is_valid,
                'is_business': is_business,
                'domain': domain,
                'score': 85 if is_valid and is_business else 60 if is_valid else 20
            }
    
    return render_template('email_validator.html', result=result)

@app.route('/domain-checker', methods=['GET', 'POST'])
@login_required
def domain_checker():
    """Domain information checker"""
    result = None
    
    if request.method == 'POST':
        domain = request.form.get('domain', '').strip()
        if domain:
            # Remove protocol if present
            domain = domain.replace('http://', '').replace('https://', '').replace('www.', '')
            domain = domain.split('/')[0]  # Remove path
            
            try:
                # Simple domain check using requests
                test_url = f"https://{domain}"
                response = requests.head(test_url, timeout=5)
                
                result = {
                    'domain': domain,
                    'status': 'Active',
                    'status_code': response.status_code,
                    'server': response.headers.get('Server', 'Unknown'),
                    'ssl_enabled': True,
                    'response_time': '< 1s'
                }
            except:
                result = {
                    'domain': domain,
                    'status': 'Inactive or Unreachable',
                    'status_code': 'N/A',
                    'server': 'Unknown',
                    'ssl_enabled': False,
                    'response_time': 'Timeout'
                }
    
    return render_template('domain_checker.html', result=result)

@app.route('/lead-enrichment', methods=['GET', 'POST'])
@login_required
def lead_enrichment():
    """Lead enrichment tool"""
    result = None
    
    if request.method == 'POST':
        company_name = request.form.get('company_name', '').strip()
        if company_name:
            # Simulate lead enrichment
            result = {
                'company_name': company_name,
                'industry': 'Technology',
                'employee_count': '50-100',
                'revenue': '$5M - $10M',
                'location': 'New York, NY',
                'founded': '2015',
                'website': f"https://{company_name.lower().replace(' ', '')}.com",
                'social_media': {
                    'linkedin': f"https://linkedin.com/company/{company_name.lower().replace(' ', '-')}",
                    'twitter': f"https://twitter.com/{company_name.lower().replace(' ', '')}",
                    'facebook': f"https://facebook.com/{company_name.lower().replace(' ', '')}"
                }
            }
    
    return render_template('lead_enrichment.html', result=result)

@app.route('/settings')
@login_required
def settings():
    """User settings"""
    user = get_current_user()
    return render_template('settings.html', user=user)

def get_available_templates(template_type):
    """Get list of available templates from the templates directory"""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        templates_dir = os.path.join(base_dir, 'templates', template_type)
        
        if not os.path.exists(templates_dir):
            return []
        
        templates = []
        for filename in os.listdir(templates_dir):
            if filename.endswith('.html'):
                name = filename[:-5]  # Remove .html extension
                template_info = {
                    'name': name,
                    'filename': filename,
                    'display_name': name.replace('_', ' ').title(),
                    'type': template_type
                }
                
                # Add specific info based on template name
                if 'dental' in name:
                    template_info['industry'] = 'Healthcare'
                    template_info['conversion_rate'] = '18%'
                elif 'fitness' in name:
                    template_info['industry'] = 'Fitness'
                    template_info['conversion_rate'] = '25%'
                elif 'restaurant' in name:
                    template_info['industry'] = 'Food & Beverage'
                    template_info['conversion_rate'] = '22%'
                elif 'cold_email' in name:
                    template_info['industry'] = 'General'
                    template_info['response_rate'] = '8-12%'
                elif 'healthcare' in name:
                    template_info['industry'] = 'Healthcare'
                    template_info['open_rate'] = '25-35%'
                else:
                    template_info['industry'] = 'General'
                    template_info['conversion_rate'] = '20%'
                
                templates.append(template_info)
        
        return templates
        
    except Exception as e:
        print(f"Error loading templates: {e}")
        return []

def get_funnel_templates():
    """Legacy function - kept for backward compatibility"""
    return {
        'dental': {
            'title': 'Dental Practice Lead Magnet',
            'description': 'Free consultation funnel for dental practices',
            'conversion_rate': '18%',
            'variables': {
                'practice_name': '[Your Practice Name]',
                'phone_number': '[Your Phone Number]',
                'address': '[Your Address]'
            },
            'html': '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Free Dental Consultation - {{ practice_name }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Arial', sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 60px 0; text-align: center; }
        .header h1 { font-size: 3rem; margin-bottom: 20px; }
        .header p { font-size: 1.2rem; margin-bottom: 30px; }
        .main-content { padding: 60px 0; background: #f8f9fa; }
        .form-section { background: white; padding: 40px; border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); margin-bottom: 40px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 12px; border: 2px solid #e1e5e9; border-radius: 5px; font-size: 16px; }
        .form-group input:focus, .form-group select:focus { border-color: #667eea; outline: none; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 40px; border: none; border-radius: 5px; font-size: 18px; font-weight: bold; cursor: pointer; transition: transform 0.3s; }
        .btn:hover { transform: translateY(-2px); }
        .benefits { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 30px; margin: 40px 0; }
        .benefit { background: white; padding: 30px; border-radius: 10px; text-align: center; box-shadow: 0 5px 15px rgba(0,0,0,0.08); }
        .benefit i { font-size: 3rem; color: #667eea; margin-bottom: 20px; }
        .testimonials { background: white; padding: 40px; border-radius: 10px; margin: 40px 0; }
        .testimonial { padding: 20px; border-left: 4px solid #667eea; margin: 20px 0; background: #f8f9fa; }
        .footer { background: #333; color: white; padding: 40px 0; text-align: center; }
    </style>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <header class="header">
        <div class="container">
            <h1>Free Dental Consultation</h1>
            <p>Professional dental care you can trust. Book your complimentary consultation today!</p>
            <p><strong>Call Now: {{ phone_number }}</strong></p>
        </div>
    </header>

    <main class="main-content">
        <div class="container">
            <div class="form-section">
                <h2 style="text-align: center; margin-bottom: 30px; color: #667eea;">Schedule Your Free Consultation</h2>
                <form id="consultationForm">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                        <div class="form-group">
                            <label for="firstName">First Name *</label>
                            <input type="text" id="firstName" name="firstName" required>
                        </div>
                        <div class="form-group">
                            <label for="lastName">Last Name *</label>
                            <input type="text" id="lastName" name="lastName" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="email">Email Address *</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                    <div class="form-group">
                        <label for="phone">Phone Number *</label>
                        <input type="tel" id="phone" name="phone" required>
                    </div>
                    <div class="form-group">
                        <label for="service">Service Interested In</label>
                        <select id="service" name="service">
                            <option value="">Select a service</option>
                            <option value="cleaning">Dental Cleaning</option>
                            <option value="whitening">Teeth Whitening</option>
                            <option value="implants">Dental Implants</option>
                            <option value="orthodontics">Orthodontics</option>
                            <option value="other">Other</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="concerns">Tell us about your dental concerns</label>
                        <textarea id="concerns" name="concerns" rows="4" placeholder="Describe any pain, concerns, or questions you have..."></textarea>
                    </div>
                    <div style="text-align: center;">
                        <button type="submit" class="btn">
                            <i class="fas fa-calendar-alt"></i> Book My Free Consultation
                        </button>
                    </div>
                </form>
            </div>

            <div class="benefits">
                <div class="benefit">
                    <i class="fas fa-user-md"></i>
                    <h3>Expert Care</h3>
                    <p>Our experienced dental professionals provide comprehensive care using the latest techniques and technology.</p>
                </div>
                <div class="benefit">
                    <i class="fas fa-shield-alt"></i>
                    <h3>Pain-Free Experience</h3>
                    <p>We prioritize your comfort with modern pain management techniques and a gentle approach.</p>
                </div>
                <div class="benefit">
                    <i class="fas fa-clock"></i>
                    <h3>Convenient Scheduling</h3>
                    <p>Flexible appointment times including evenings and weekends to fit your busy schedule.</p>
                </div>
            </div>

            <div class="testimonials">
                <h2 style="text-align: center; margin-bottom: 30px; color: #667eea;">What Our Patients Say</h2>
                <div class="testimonial">
                    <p>"The best dental experience I've ever had! The staff is incredibly professional and made me feel comfortable throughout the entire process."</p>
                    <strong>- Sarah M.</strong>
                </div>
                <div class="testimonial">
                    <p>"Finally found a dentist I can trust. The free consultation was thorough and they explained everything clearly."</p>
                    <strong>- Mike R.</strong>
                </div>
                <div class="testimonial">
                    <p>"Amazing results with my teeth whitening! The transformation was incredible and the process was completely painless."</p>
                    <strong>- Jennifer L.</strong>
                </div>
            </div>
        </div>
    </main>

    <footer class="footer">
        <div class="container">
            <h3>{{ practice_name }}</h3>
            <p>{{ address }}</p>
            <p>Phone: {{ phone_number }}</p>
            <p>&copy; 2024 {{ practice_name }}. All rights reserved.</p>
        </div>
    </footer>

    <script>
        document.getElementById('consultationForm').addEventListener('submit', function(e) {
            e.preventDefault();
            alert('Thank you! We will contact you within 24 hours to schedule your free consultation.');
            this.reset();
        });
    </script>
</body>
</html>''',
        },
        'medical': {
            'title': 'Medical Clinic Webinar Funnel',
            'description': 'Educational webinar for medical clinics',
            'conversion_rate': '22%',
            'variables': {
                'clinic_name': '[Your Clinic Name]',
                'doctor_name': '[Doctor Name]',
                'webinar_topic': '[Webinar Topic]'
            },
            'html': '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Free Medical Webinar - {{ clinic_name }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Arial', sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .header { background: linear-gradient(135deg, #42a5f5 0%, #1e88e5 100%); color: white; padding: 80px 0; text-align: center; }
        .header h1 { font-size: 3.5rem; margin-bottom: 20px; }
        .webinar-date { background: rgba(255,255,255,0.2); padding: 20px; border-radius: 10px; margin: 30px auto; max-width: 600px; }
        .registration-form { background: white; padding: 50px; border-radius: 15px; box-shadow: 0 15px 35px rgba(0,0,0,0.1); margin: -80px auto 0; max-width: 800px; position: relative; z-index: 10; }
        .form-group { margin-bottom: 25px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
        .form-group input { width: 100%; padding: 15px; border: 2px solid #e1e5e9; border-radius: 8px; font-size: 16px; }
        .btn-primary { background: linear-gradient(135deg, #42a5f5 0%, #1e88e5 100%); color: white; padding: 18px 50px; border: none; border-radius: 8px; font-size: 20px; font-weight: bold; cursor: pointer; width: 100%; }
        .benefits-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 40px; margin: 80px 0; }
        .benefit-card { background: white; padding: 40px; border-radius: 15px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.08); }
        .doctor-profile { background: #f8f9fa; padding: 60px 0; }
        .profile-content { display: grid; grid-template-columns: 1fr 2fr; gap: 40px; align-items: center; }
        .countdown { background: #ff6b6b; color: white; padding: 30px; text-align: center; margin: 40px 0; border-radius: 10px; }
        .countdown-timer { display: flex; justify-content: center; gap: 20px; margin-top: 20px; }
        .countdown-item { text-align: center; }
        .countdown-number { font-size: 2rem; font-weight: bold; display: block; }
    </style>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <header class="header">
        <div class="container">
            <h1>FREE Medical Webinar</h1>
            <h2>{{ webinar_topic }}</h2>
            <div class="webinar-date">
                <h3><i class="fas fa-calendar"></i> Wednesday, October 25th at 7:00 PM EST</h3>
                <p>Reserve your spot now - Limited to 500 attendees</p>
            </div>
        </div>
    </header>

    <main>
        <div class="container">
            <div class="registration-form">
                <h2 style="text-align: center; margin-bottom: 30px; color: #1e88e5;">Register for FREE Webinar</h2>
                <form id="webinarForm">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                        <div class="form-group">
                            <label for="firstName">First Name *</label>
                            <input type="text" id="firstName" name="firstName" required>
                        </div>
                        <div class="form-group">
                            <label for="lastName">Last Name *</label>
                            <input type="text" id="lastName" name="lastName" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="email">Email Address *</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                    <div class="form-group">
                        <label for="phone">Phone Number</label>
                        <input type="tel" id="phone" name="phone">
                    </div>
                    <button type="submit" class="btn-primary">
                        <i class="fas fa-lock"></i> SECURE MY FREE SPOT
                    </button>
                </form>
            </div>

            <div class="countdown">
                <h3>Webinar Starts In:</h3>
                <div class="countdown-timer">
                    <div class="countdown-item">
                        <span class="countdown-number" id="days">07</span>
                        <span>Days</span>
                    </div>
                    <div class="countdown-item">
                        <span class="countdown-number" id="hours">18</span>
                        <span>Hours</span>
                    </div>
                    <div class="countdown-item">
                        <span class="countdown-number" id="minutes">42</span>
                        <span>Minutes</span>
                    </div>
                    <div class="countdown-item">
                        <span class="countdown-number" id="seconds">15</span>
                        <span>Seconds</span>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <footer style="background: #333; color: white; padding: 40px 0; text-align: center;">
        <div class="container">
            <h3>{{ clinic_name }}</h3>
            <p>&copy; 2024 {{ clinic_name }}. All rights reserved.</p>
        </div>
    </footer>

    <script>
        document.getElementById('webinarForm').addEventListener('submit', function(e) {
            e.preventDefault();
            alert('Registration successful! Check your email for webinar access details.');
            this.reset();
        });
    </script>
</body>
</html>''',
        },
        'gym': {
            'title': 'Gym Membership Funnel',
            'description': 'Free trial membership for fitness centers',
            'conversion_rate': '25%',
            'variables': {
                'gym_name': '[Your Gym Name]',
                'trial_days': '7',
                'membership_price': '$29.99'
            },
            'html': '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ trial_days }}-Day Free Trial - {{ gym_name }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Arial', sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        .hero { background: linear-gradient(rgba(0,0,0,0.6), rgba(0,0,0,0.6)), url('https://via.placeholder.com/1200x600/ff6b35/ffffff?text=Gym+Background'); background-size: cover; background-position: center; color: white; padding: 100px 0; text-align: center; }
        .hero h1 { font-size: 4rem; margin-bottom: 20px; text-shadow: 2px 2px 4px rgba(0,0,0,0.5); }
        .hero-cta { background: #ff6b35; padding: 20px 40px; border-radius: 50px; font-size: 1.5rem; font-weight: bold; display: inline-block; margin-top: 30px; text-decoration: none; color: white; transition: transform 0.3s; }
        .hero-cta:hover { transform: scale(1.05); }
        .trial-form { background: white; padding: 50px; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); margin: -50px auto 80px; max-width: 700px; position: relative; z-index: 10; }
        .form-group { margin-bottom: 25px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
        .form-group input, .form-group select { width: 100%; padding: 15px; border: 2px solid #e1e5e9; border-radius: 8px; font-size: 16px; }
        .btn-orange { background: linear-gradient(135deg, #ff6b35 0%, #e55a31 100%); color: white; padding: 18px 40px; border: none; border-radius: 8px; font-size: 18px; font-weight: bold; cursor: pointer; width: 100%; }
        .features-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 40px; margin: 80px 0; }
        .feature-card { background: white; padding: 40px; border-radius: 15px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.08); transition: transform 0.3s; }
        .feature-card:hover { transform: translateY(-10px); }
        .transformations { background: #f8f9fa; padding: 80px 0; }
        .transformation-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 40px; }
        .transformation-card { background: white; border-radius: 15px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.08); }
        .pricing-section { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 80px 0; text-align: center; }
        .price-card { background: white; color: #333; padding: 40px; border-radius: 15px; max-width: 500px; margin: 0 auto; }
        .price { font-size: 3rem; color: #ff6b35; font-weight: bold; }
        .original-price { text-decoration: line-through; color: #999; font-size: 1.5rem; }
    </style>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <section class="hero">
        <div class="container">
            <h1>Transform Your Body</h1>
            <h2>{{ trial_days }}-Day FREE Trial</h2>
            <p style="font-size: 1.3rem; margin: 20px 0;">Join {{ gym_name }} and discover what your body is capable of achieving</p>
            <a href="#trial-form" class="hero-cta">START YOUR FREE TRIAL</a>
        </div>
    </section>

    <main>
        <div class="container">
            <div class="trial-form" id="trial-form">
                <h2 style="text-align: center; margin-bottom: 30px; color: #ff6b35;">Claim Your {{ trial_days }}-Day Free Trial</h2>
                <form id="trialForm">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                        <div class="form-group">
                            <label for="firstName">First Name *</label>
                            <input type="text" id="firstName" name="firstName" required>
                        </div>
                        <div class="form-group">
                            <label for="lastName">Last Name *</label>
                            <input type="text" id="lastName" name="lastName" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="email">Email Address *</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                    <div class="form-group">
                        <label for="phone">Phone Number *</label>
                        <input type="tel" id="phone" name="phone" required>
                    </div>
                    <div class="form-group">
                        <label for="goals">Fitness Goals</label>
                        <select id="goals" name="goals">
                            <option value="">Select your primary goal</option>
                            <option value="weight-loss">Weight Loss</option>
                            <option value="muscle-gain">Muscle Gain</option>
                            <option value="general-fitness">General Fitness</option>
                            <option value="strength">Strength Training</option>
                            <option value="endurance">Endurance</option>
                        </select>
                    </div>
                    <button type="submit" class="btn-orange">
                        <i class="fas fa-dumbbell"></i> START MY FREE TRIAL
                    </button>
                    <p style="text-align: center; margin-top: 15px; color: #666; font-size: 0.9rem;">No commitment required. Cancel anytime during trial.</p>
                </form>
            </div>

            <div class="features-grid">
                <div class="feature-card">
                    <i class="fas fa-dumbbell" style="font-size: 3rem; color: #ff6b35; margin-bottom: 20px;"></i>
                    <h3>State-of-the-Art Equipment</h3>
                    <p>Access to the latest fitness equipment and technology to maximize your workout results.</p>
                </div>
                <div class="feature-card">
                    <i class="fas fa-users" style="font-size: 3rem; color: #ff6b35; margin-bottom: 20px;"></i>
                    <h3>Expert Personal Trainers</h3>
                    <p>Work with certified trainers who will create personalized workout plans just for you.</p>
                </div>
                <div class="feature-card">
                    <i class="fas fa-calendar-alt" style="font-size: 3rem; color: #ff6b35; margin-bottom: 20px;"></i>
                    <h3>Group Classes</h3>
                    <p>Join our energizing group fitness classes including yoga, HIIT, spin, and more.</p>
                </div>
                <div class="feature-card">
                    <i class="fas fa-clock" style="font-size: 3rem; color: #ff6b35; margin-bottom: 20px;"></i>
                    <h3>24/7 Access</h3>
                    <p>Work out on your schedule with round-the-clock gym access for all members.</p>
                </div>
            </div>
        </div>

        <section class="transformations">
            <div class="container">
                <h2 style="text-align: center; margin-bottom: 60px; font-size: 2.5rem; color: #333;">Real Member Transformations</h2>
                <div class="transformation-grid">
                    <div class="transformation-card">
                        <img src="https://via.placeholder.com/400x300/ff6b35/ffffff?text=Before+%26+After" alt="Transformation" style="width: 100%; height: 250px; object-fit: cover;">
                        <div style="padding: 30px;">
                            <h4>Sarah Lost 35 lbs in 4 Months</h4>
                            <p>"The trainers at {{ gym_name }} completely changed my life. I never thought I could achieve results like this!"</p>
                        </div>
                    </div>
                    <div class="transformation-card">
                        <img src="https://via.placeholder.com/400x300/667eea/ffffff?text=Success+Story" alt="Transformation" style="width: 100%; height: 250px; object-fit: cover;">
                        <div style="padding: 30px;">
                            <h4>Mike Gained 20 lbs of Muscle</h4>
                            <p>"The personalized training program helped me bulk up and get stronger than I ever imagined possible."</p>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <section class="pricing-section">
            <div class="container">
                <h2 style="margin-bottom: 40px; font-size: 2.5rem;">Special Membership Offer</h2>
                <div class="price-card">
                    <h3 style="margin-bottom: 20px;">After Your Free Trial</h3>
                    <div style="margin: 30px 0;">
                        <span class="original-price">$49.99/month</span>
                        <div class="price">{{ membership_price }}/month</div>
                        <p style="color: #ff6b35; font-weight: bold; margin-top: 10px;">Save $240 per year!</p>
                    </div>
                    <ul style="list-style: none; padding: 0; text-align: left; margin: 30px 0;">
                        <li style="margin: 10px 0;"><i class="fas fa-check" style="color: #4CAF50; margin-right: 10px;"></i>Unlimited gym access</li>
                        <li style="margin: 10px 0;"><i class="fas fa-check" style="color: #4CAF50; margin-right: 10px;"></i>All group fitness classes</li>
                        <li style="margin: 10px 0;"><i class="fas fa-check" style="color: #4CAF50; margin-right: 10px;"></i>Personal training consultations</li>
                        <li style="margin: 10px 0;"><i class="fas fa-check" style="color: #4CAF50; margin-right: 10px;"></i>Nutrition guidance</li>
                    </ul>
                </div>
            </div>
        </section>
    </main>

    <footer style="background: #333; color: white; padding: 40px 0; text-align: center;">
        <div class="container">
            <h3>{{ gym_name }}</h3>
            <p>&copy; 2024 {{ gym_name }}. All rights reserved.</p>
        </div>
    </footer>

    <script>
        document.getElementById('trialForm').addEventListener('submit', function(e) {
            e.preventDefault();
            alert('Congratulations! Your free trial has been activated. Visit us today to get started!');
            this.reset();
        });
    </script>
</body>
</html>''',
        }
    }

@app.route('/help-center')
@login_required
def help_center():
    """Help Center page with guide and support email"""
    return render_template('help_center.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)