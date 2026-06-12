-- Migration: Add new property fields for comprehensive data extraction
-- Date: 2024

-- Add land area field
ALTER TABLE properties ADD COLUMN IF NOT EXISTS land_area INTEGER;

-- Add built area field (in case different from area)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS built_area INTEGER;

-- Add frontage/building width field
ALTER TABLE properties ADD COLUMN IF NOT EXISTS frontage INTEGER;

-- Add usage type field
ALTER TABLE properties ADD COLUMN IF NOT EXISTS usage_type VARCHAR(100);

-- Add building age description
ALTER TABLE properties ADD COLUMN IF NOT EXISTS building_age VARCHAR(50);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_properties_land_area ON properties(land_area);
CREATE INDEX IF NOT EXISTS idx_properties_frontage ON properties(frontage);
CREATE INDEX IF NOT EXISTS idx_properties_usage_type ON properties(usage_type);

-- Add comments
COMMENT ON COLUMN properties.land_area IS 'Land area in square meters (متراژ زمین)';
COMMENT ON COLUMN properties.built_area IS 'Built/construction area (زیربنا)';
COMMENT ON COLUMN properties.frontage IS 'Building frontage/width in meters (بر)';
COMMENT ON COLUMN properties.usage_type IS 'Type of usage - residential, commercial, etc.';
COMMENT ON COLUMN properties.building_age IS 'Building age description';
