# Overview

AI Sales Machine is a comprehensive lead generation and outreach automation platform built with Flask. The application enables businesses to discover qualified prospects, organize leads through intelligent classification, and execute targeted outreach campaigns. The platform provides a complete sales pipeline from prospect discovery to conversion tracking, featuring web scraping capabilities, automated lead scoring, email campaign templates, sales funnels, and analytics dashboards.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Frontend Architecture
The application uses a server-side rendered architecture with Flask's Jinja2 templating engine. The frontend is built with Bootstrap 5 for responsive design and includes modern typography using Space Grotesk fonts and Phosphor icons. The interface features a warm color palette with morphism design elements, creating a modern and professional user experience across all modules including lead discovery, classification, outreach management, and analytics.

## Backend Architecture
Built on Flask with a modular route-based structure:
- **Session Management**: Uses Flask's built-in session handling with configurable secret keys from environment variables
- **Web Scraping Engine**: Custom LeadScraper class with realistic browser headers and session management to avoid anti-bot detection
- **Lead Processing Pipeline**: Structured workflow for search query processing, multi-source scraping, data extraction, and automatic lead classification
- **File-based Storage**: Server-side JSON file storage for user leads, avoiding database complexity while maintaining data persistence
- **Concurrent Processing**: ThreadPoolExecutor for parallel web scraping operations to improve performance

## Data Storage Solutions
The application uses a hybrid approach:
- **Session Storage**: Flask sessions for temporary user state and authentication
- **File-based Persistence**: JSON files stored in `user_data/` directory for lead data, organized by username
- **Template Management**: JSON manifest files for campaign and funnel templates
- **No Database**: Deliberately avoids database dependencies for simplified deployment and maintenance

## Authentication and Authorization
Simple session-based authentication using Flask's session management. Users are identified by username stored in session, which determines their lead data file path. No complex password management or user registration system is implemented, focusing on simplicity and rapid deployment.

## Lead Processing Pipeline
Structured data extraction and classification system:
1. **Multi-source Scraping**: Targets Yellow Pages and business directories using BeautifulSoup and Trafilatura
2. **Data Enhancement**: Extracts business names, phone numbers, addresses, websites, emails, and social media profiles
3. **Intelligent Classification**: Automatically categorizes leads as Premium (email + website + phone), Sales-Ready (phone + website), Prospect (phone only), Website (website only), or Social (social media only)
4. **Priority Scoring**: Assigns scores based on available contact information and geographic tier
5. **Export Capabilities**: CSV export functionality for CRM integration

# External Dependencies

## Core Web Framework
- **Flask 3.1.2**: Python web framework for routing, templating, and session management
- **Gunicorn**: WSGI server for production deployment
- **Jinja2**: Template engine integrated with Flask for dynamic HTML generation

## Web Scraping and Data Processing
- **Requests 2.32.5**: HTTP client library with session management and custom headers
- **BeautifulSoup4 4.13.5**: HTML/XML parsing for extracting structured data from web pages
- **Trafilatura 1.6.3**: Content extraction library optimized for web scraping
- **urllib.parse**: URL manipulation and encoding utilities

## Advanced Anti-Bot Detection
- **Undetected ChromeDriver 3.5.5**: Stealth browser automation to bypass detection
- **Selenium 4.15.2**: Browser automation framework for complex scraping scenarios
- **Selenium Stealth 1.0.6**: Additional stealth capabilities for browser automation
- **Botasaurus 4.0.88**: Advanced anti-detection scraping framework
- **Fake UserAgent 1.4.0**: Dynamic user agent rotation
- **CloudScraper 1.2.71**: Cloudflare bypass capabilities
- **Requests HTML 0.10.0**: JavaScript-enabled web scraping

## Frontend Libraries (CDN)
- **Bootstrap 5.3.0**: Responsive CSS framework for modern UI components
- **Space Grotesk & JetBrains Mono**: Custom typography for professional appearance
- **Phosphor Icons**: Modern icon library for consistent visual elements
- **DataTables 1.13.6**: Advanced table functionality with sorting and filtering
- **Chart.js**: Analytics visualization and reporting dashboards

## Data Processing Libraries
- **Babel 2.17.0**: Internationalization support
- **DateParser 1.2.2**: Advanced date parsing capabilities
- **Python-dateutil 2.9.0**: Date manipulation utilities
- **Regex 2025.9.1**: Enhanced regular expression support for data extraction