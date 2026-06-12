-- Initialize Database Schema
-- SorinFlow Divar Scraper

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Create cities table
CREATE TABLE IF NOT EXISTS cities (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE,
    province VARCHAR(100),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create categories table
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(id),
    url_path VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create properties table
CREATE TABLE IF NOT EXISTS properties (
    id SERIAL PRIMARY KEY,
    tag_number VARCHAR(50) UNIQUE NOT NULL,
    divar_id VARCHAR(50) UNIQUE NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    price BIGINT,
    price_per_meter BIGINT,
    total_price BIGINT,
    rent_price BIGINT,
    deposit BIGINT,
    area INTEGER,
    land_area INTEGER,
    built_area INTEGER,
    rooms INTEGER,
    year_built INTEGER,
    floor INTEGER,
    total_floors INTEGER,
    has_elevator BOOLEAN DEFAULT false,
    has_parking BOOLEAN DEFAULT false,
    has_storage BOOLEAN DEFAULT false,
    has_balcony BOOLEAN DEFAULT false,
    has_images BOOLEAN DEFAULT false,
    building_direction VARCHAR(50),
    frontage INTEGER,
    unit_status VARCHAR(50),
    document_type VARCHAR(100),
    usage_type VARCHAR(100),
    building_age VARCHAR(50),
    
    -- Location
    city_id INTEGER REFERENCES cities(id),
    city_name VARCHAR(100),
    district VARCHAR(200),
    neighborhood VARCHAR(200),
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    
    -- Category
    category_id INTEGER REFERENCES categories(id),
    category_name VARCHAR(100),
    property_type VARCHAR(50),
    listing_type VARCHAR(50),
    
    -- Contact
    phone_number VARCHAR(20),
    seller_name VARCHAR(200),
    
    -- URLs
    url VARCHAR(500) NOT NULL,
    
    -- Images
    images JSONB DEFAULT '[]'::jsonb,
    thumbnail_url VARCHAR(500),
    images_downloaded BOOLEAN DEFAULT false,
    
    -- Features and Amenities
    features JSONB DEFAULT '[]'::jsonb,
    amenities JSONB DEFAULT '[]'::jsonb,
    
    -- Raw data
    raw_data JSONB,
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    is_verified BOOLEAN DEFAULT false,
    
    -- Timestamps
    posted_at TIMESTAMP WITH TIME ZONE,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create scraping_jobs table
CREATE TABLE IF NOT EXISTS scraping_jobs (
    id SERIAL PRIMARY KEY,
    job_id UUID DEFAULT uuid_generate_v4() UNIQUE NOT NULL,
    city_id INTEGER REFERENCES cities(id),
    category_id INTEGER REFERENCES categories(id),
    status VARCHAR(50) DEFAULT 'pending',
    total_pages INTEGER DEFAULT 0,
    scraped_pages INTEGER DEFAULT 0,
    total_items INTEGER DEFAULT 0,
    scraped_items INTEGER DEFAULT 0,
    new_items INTEGER DEFAULT 0,
    updated_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create cookies table
CREATE TABLE IF NOT EXISTS cookies (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR(20) NOT NULL,
    cookies JSONB NOT NULL,
    token TEXT,
    is_valid BOOLEAN DEFAULT true,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create proxies table
CREATE TABLE IF NOT EXISTS proxies (
    id SERIAL PRIMARY KEY,
    address VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    username VARCHAR(100),
    password VARCHAR(100),
    protocol VARCHAR(20) DEFAULT 'http',
    is_active BOOLEAN DEFAULT true,
    is_working BOOLEAN DEFAULT true,
    last_checked TIMESTAMP WITH TIME ZONE,
    fail_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    avg_response_time FLOAT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create scraping_logs table
CREATE TABLE IF NOT EXISTS scraping_logs (
    id SERIAL PRIMARY KEY,
    job_id UUID REFERENCES scraping_jobs(job_id),
    level VARCHAR(20),
    message TEXT,
    details JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_properties_divar_id ON properties(divar_id);
CREATE INDEX IF NOT EXISTS idx_properties_tag_number ON properties(tag_number);
CREATE INDEX IF NOT EXISTS idx_properties_city_id ON properties(city_id);
CREATE INDEX IF NOT EXISTS idx_properties_category_id ON properties(category_id);
CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
CREATE INDEX IF NOT EXISTS idx_properties_area ON properties(area);
CREATE INDEX IF NOT EXISTS idx_properties_rooms ON properties(rooms);
CREATE INDEX IF NOT EXISTS idx_properties_scraped_at ON properties(scraped_at);
CREATE INDEX IF NOT EXISTS idx_properties_phone_number ON properties(phone_number);
CREATE INDEX IF NOT EXISTS idx_properties_listing_type ON properties(listing_type);
CREATE INDEX IF NOT EXISTS idx_properties_property_type ON properties(property_type);
CREATE INDEX IF NOT EXISTS idx_properties_title_trgm ON properties USING gin(title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_scraping_jobs_status ON scraping_jobs(status);
CREATE INDEX IF NOT EXISTS idx_scraping_jobs_job_id ON scraping_jobs(job_id);

CREATE INDEX IF NOT EXISTS idx_cookies_phone_number ON cookies(phone_number);
CREATE INDEX IF NOT EXISTS idx_cookies_is_valid ON cookies(is_valid);

-- Insert default cities
INSERT INTO cities (name, slug, province) VALUES
    ('تهران', 'tehran', 'تهران'),
    ('ارومیه', 'urmia', 'آذربایجان غربی'),
    ('تبریز', 'tabriz', 'آذربایجان شرقی'),
    ('اصفهان', 'isfahan', 'اصفهان'),
    ('شیراز', 'shiraz', 'فارس'),
    ('مشهد', 'mashhad', 'خراسان رضوی'),
    ('کرج', 'karaj', 'البرز'),
    ('اهواز', 'ahvaz', 'خوزستان'),
    ('قم', 'qom', 'قم'),
    ('کرمانشاه', 'kermanshah', 'کرمانشاه'),
    ('رشت', 'rasht', 'گیلان'),
    ('کرمان', 'kerman', 'کرمان'),
    ('ساری', 'sari', 'مازندران'),
    ('یزد', 'yazd', 'یزد'),
    ('اردبیل', 'ardabil', 'اردبیل'),
    ('بندرعباس', 'bandar-abbas', 'هرمزگان'),
    ('زنجان', 'zanjan', 'زنجان'),
    ('سنندج', 'sanandaj', 'کردستان'),
    ('همدان', 'hamadan', 'همدان'),
    ('گرگان', 'gorgan', 'گلستان')
ON CONFLICT (slug) DO NOTHING;

-- Insert default categories
INSERT INTO categories (name, slug, url_path) VALUES
    ('خرید مسکونی', 'buy-residential', '/s/{city}/buy-residential'),
    ('خرید آپارتمان', 'buy-apartment', '/s/{city}/buy-apartment'),
    ('خرید ویلا', 'buy-villa', '/s/{city}/buy-villa'),
    ('خرید خانه کلنگی', 'buy-old-house', '/s/{city}/buy-old-house'),
    ('اجاره مسکونی', 'rent-residential', '/s/{city}/rent-residential'),
    ('اجاره آپارتمان', 'rent-apartment', '/s/{city}/rent-apartment'),
    ('اجاره ویلا', 'rent-villa', '/s/{city}/rent-villa'),
    ('خرید اداری و تجاری', 'buy-commercial-property', '/s/{city}/buy-commercial-property'),
    ('خرید دفتر کار', 'buy-office', '/s/{city}/buy-office'),
    ('خرید مغازه', 'buy-store', '/s/{city}/buy-store'),
    ('خرید صنعتی و کشاورزی', 'buy-industrial-agricultural-property', '/s/{city}/buy-industrial-agricultural-property'),
    ('اجاره اداری و تجاری', 'rent-commercial-property', '/s/{city}/rent-commercial-property'),
    ('اجاره دفتر کار', 'rent-office', '/s/{city}/rent-office'),
    ('اجاره مغازه', 'rent-store', '/s/{city}/rent-store'),
    ('اجاره صنعتی و کشاورزی', 'rent-industrial-agricultural-property', '/s/{city}/rent-industrial-agricultural-property'),
    ('اجاره کوتاه مدت', 'rent-temporary', '/s/{city}/rent-temporary'),
    ('خدمات املاک', 'real-estate-services', '/s/{city}/real-estate-services')
ON CONFLICT (slug) DO NOTHING;

-- Insert default proxies
INSERT INTO proxies (address, port) VALUES
    ('45.83.181.214', 57623),
    ('146.19.143.69', 11599)
ON CONFLICT DO NOTHING;
