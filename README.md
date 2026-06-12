<div align="center">

# 🏠 SorinFlow Divar Scraper

### Enterprise-Grade Web Scraping Solution for Divar.ir

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.41-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)

**A comprehensive, production-ready web scraping system for Divar.ir - Iran's largest classified ads platform**

[Features](#-key-features) • [Quick Start](#-quick-start) • [Documentation](#-api-documentation) • [Architecture](#-architecture) • [Support](#-support)

</div>

---

## 🎯 Project Overview

SorinFlow Divar Scraper is an **enterprise-grade automation solution** designed to extract comprehensive property data from Divar.ir with advanced anti-detection capabilities, authenticated phone number extraction, and real-time analytics dashboard.

### 🚀 What Makes This Project Special?

```
✅ Production-Ready Architecture    ✅ Advanced Anti-Detection System
✅ Authenticated Data Extraction    ✅ Real-Time Analytics Dashboard  
✅ Scalable & Performant           ✅ One-Command Docker Deployment
✅ 100% Async Operations           ✅ Enterprise Security Standards
```

---

## 💎 Key Features

<table>
<tr>
<td width="50%">

### 🔐 Authentication & Security
- **OTP-Based Login**: Secure phone authentication
- **Cookie Management**: Automatic session persistence
- **Stealth Mode**: Advanced anti-bot detection
- **Proxy Rotation**: Multiple proxy support
- **Rate Limiting**: Intelligent request throttling
- **Encrypted Storage**: Secure credential management

</td>
<td width="50%">

### 🏗️ Technical Excellence
- **Async Architecture**: Non-blocking I/O operations
- **RESTful API**: Comprehensive FastAPI endpoints
- **Real-Time Dashboard**: RTL Persian UI
- **Database ORM**: SQLAlchemy async support
- **Redis Caching**: High-performance data layer
- **Docker Compose**: One-command deployment

</td>
</tr>
<tr>
<td width="50%">

### 📊 Data Extraction
- **Property Details**: Complete listing information
- **Phone Numbers**: Authenticated contact extraction
- **Images**: Automatic download & storage
- **Amenities**: Full feature parsing
- **Pricing**: Real-time market data
- **Location**: GPS coordinates & addresses

</td>
<td width="50%">

### 📈 Analytics & Monitoring
- **Job Tracking**: Real-time scraping progress
- **Statistics**: City-wise analytics
- **Error Logging**: Comprehensive debugging
- **Performance Metrics**: Response time tracking
- **Export Functionality**: CSV/JSON data export
- **Health Monitoring**: System status checks

</td>
</tr>
</table>

---

## 🏆 Technical Stack

<div align="center">

### Backend & Core
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)

### Database & Cache
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white)

### DevOps & Infrastructure
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Nginx](https://img.shields.io/badge/Nginx-009639?style=for-the-badge&logo=nginx&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)

### Frontend
![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white)
![CSS3](https://img.shields.io/badge/CSS3-1572B6?style=for-the-badge&logo=css3&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)
![Bootstrap](https://img.shields.io/badge/Bootstrap-7952B3?style=for-the-badge&logo=bootstrap&logoColor=white)

</div>

---

## 🚀 Quick Start

### Prerequisites

```bash
✅ Docker & Docker Compose (20.10+)
✅ 2GB+ RAM
✅ 20GB+ Disk Space
✅ Linux/macOS/Windows (WSL2)
```

### Installation (3 Simple Steps)

```bash
# 1️⃣ Clone the repository
git clone https://github.com/sobhanaz/sorinflow-divar-scraper.git
cd sorinflow-divar-scraper

# 2️⃣ Configure environment (optional - has sensible defaults)
cp .env.example .env
nano .env  # Edit if needed

# 3️⃣ Launch with Docker Compose
docker compose up -d
```

### 🎉 That's It! Your scraper is now running at:

- 🌐 **Dashboard**: http://localhost/dashboard
- 📚 **API Docs**: http://localhost:8000/api/docs
- 💻 **API Base**: http://localhost:8000/api

---

## 📚 API Documentation

### 🔐 Authentication Endpoints

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| `POST` | `/api/auth/login` | Request OTP code | `{"phone": "09xxxxxxxxx"}` |
| `POST` | `/api/auth/verify` | Verify OTP & login | `{"phone": "09xxx", "code": "12345"}` |
| `GET` | `/api/auth/status` | Check login status | - |
| `GET` | `/api/auth/cookies` | List saved cookies | - |
| `DELETE` | `/api/auth/cookies/{id}` | Delete cookie session | - |


### 🤖 Scraper Endpoints

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| `POST` | `/api/scraper/start` | Start scraping job | `{"city": "urmia", "category": "buy-apartment", "max_pages": 10}` |
| `POST` | `/api/scraper/jobs/{id}/cancel` | Stop running job | - |
| `GET` | `/api/scraper/jobs` | List all jobs | Query: `?status=running&limit=20` |
| `GET` | `/api/scraper/jobs/{id}` | Job details & progress | - |
| `GET` | `/api/scraper/active-tasks` | Active scraping tasks | - |

---

<div align="center">

### Built with ❤️ by [Sobhan Azimzadeh](https://github.com/sobhanaz)

**CEO & Technical Leader @ [TECSO](https://tecso.team/) Digital Agency**

[![Portfolio](https://img.shields.io/badge/Portfolio-tecso.team-blue?style=flat-square)](https://tecso.team/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0077B5?style=flat-square&logo=linkedin)](https://www.linkedin.com/in/sobhan-azimzadeh-b956aa234)
[![GitHub](https://img.shields.io/github/followers/sobhanaz?style=flat-square&logo=github)](https://github.com/sobhanaz)

*Transforming Ideas Into Profitable Digital Solutions Since 2018*

---

⭐ **Star this repo if you find it useful!** ⭐

Last Updated: January 2026

</div>
