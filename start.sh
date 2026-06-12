#!/bin/bash

# ===========================================
# SorinFlow Divar Scraper - Startup Script
# ===========================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "=============================================="
echo "  SorinFlow Divar Scraper - Setup & Deploy"
echo "=============================================="
echo -e "${NC}"

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Docker is installed${NC}"

# Check if Docker Compose is available
if ! docker compose version &> /dev/null; then
    echo -e "${RED}Docker Compose is not available. Please install Docker Compose.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Docker Compose is available${NC}"

# Create .env file if not exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env file from template...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file${NC}"
    echo -e "${YELLOW}⚠ Please edit .env file to configure your settings${NC}"
fi

# Create necessary directories
echo -e "${BLUE}Creating directories...${NC}"
mkdir -p data/images data/cookies logs nginx/ssl

# Set permissions
chmod -R 755 data logs

echo -e "${GREEN}✓ Directories created${NC}"

# Stop existing containers
echo -e "${BLUE}Stopping existing containers...${NC}"
docker compose down 2>/dev/null || true

# Pull/Build images
echo -e "${BLUE}Building Docker images...${NC}"
docker compose build --no-cache

# Start services
echo -e "${BLUE}Starting services...${NC}"
docker compose up -d

# Wait for services to be ready
echo -e "${BLUE}Waiting for services to start...${NC}"
sleep 10

# Check service health
echo -e "${BLUE}Checking service health...${NC}"

# Check database
if docker compose exec -T db pg_isready -U sorinflow -d divar_scraper &> /dev/null; then
    echo -e "${GREEN}✓ PostgreSQL is ready${NC}"
else
    echo -e "${RED}✗ PostgreSQL is not ready${NC}"
fi

# Check Redis
if docker compose exec -T redis redis-cli -a redis_secret_2024 ping &> /dev/null; then
    echo -e "${GREEN}✓ Redis is ready${NC}"
else
    echo -e "${RED}✗ Redis is not ready${NC}"
fi

# Check Backend
if curl -s http://localhost:8000/health > /dev/null; then
    echo -e "${GREEN}✓ Backend is ready${NC}"
else
    echo -e "${YELLOW}⚠ Backend may still be starting...${NC}"
fi

# Display access information
echo ""
echo -e "${GREEN}=============================================="
echo "  Deployment Complete!"
echo "=============================================="
echo -e "${NC}"
echo -e "Access your application:"
echo -e "  ${BLUE}Dashboard:${NC} http://171.22.182.91/"
echo -e "  ${BLUE}API Docs:${NC}  http://171.22.182.91/api/docs"
echo -e "  ${BLUE}Domain:${NC}    https://scc.sorinflow.com"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "  1. Login to Divar at /dashboard → Authentication"
echo "  2. Configure proxies if needed"
echo "  3. Start scraping jobs"
echo ""
echo -e "View logs: ${BLUE}docker compose logs -f backend${NC}"
echo -e "Stop:      ${BLUE}docker compose down${NC}"
echo ""
