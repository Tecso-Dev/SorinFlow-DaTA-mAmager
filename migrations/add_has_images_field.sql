-- Migration: Add has_images field to properties table
-- Date: 2026-01-07
-- Description: Add boolean field to track if property has images in listing

ALTER TABLE properties 
ADD COLUMN IF NOT EXISTS has_images BOOLEAN DEFAULT FALSE;

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_properties_has_images ON properties(has_images);

-- Update existing properties with images
UPDATE properties 
SET has_images = TRUE 
WHERE images IS NOT NULL AND jsonb_array_length(images::jsonb) > 0;

-- Add comment
COMMENT ON COLUMN properties.has_images IS 'Whether property has images in the original listing';
