# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-12

### Added
- 🤖 **Initial Release**: Complete AI-powered sports betting prediction bot
- ⚽ **Soccer**: Poisson + Dixon-Coles predictions with xG, xT, PPDA, Field Tilt
- 🏀 **NBA**: Spread, Over/Under, Player props from ESPN
- ⚾ **MLB**: Run Line, Over/Under, Pitcher analytics
- 🏈 **NFL**: QB/RB/WR props, quarter projections
- 🎾 **Tennis**: Dynamic Elo rating by surface
- 🧠 **AI Analysis**: GPT-4o narrative analysis & Gemini Vision (photo OCR)
- 🎰 **Parlay Engine**: 3-tier parlay generation (safe/balanced/risky)
- 📊 **Parlay History**: Win rate calibration by sport/league/market
- 🔍 **Market Scanner**: Arbitrage detection, value bets, steam moves
- 📡 **Live Data**: SofaScore, TheSportsDB, ESPN aggregation
- 🛰️ **Auto-Scanner**: 24/7 automated odds monitoring
- 💰 **Advanced Models**: Kelly Criterion, Portfolio optimizer, Bayesian updates, RL strategy
- 📊 **Statistics**: Deep calibration analytics per confidence/sport/market
- 🐳 **Docker Support**: Multi-stage build, Railway deployment
- ✅ **CI/CD**: GitHub Actions workflows
- 📚 **Documentation**: Comprehensive README, architecture guide, API documentation
- 🧪 **Testing**: 25+ test files with 90%+ coverage

### Changed
- N/A (Initial release)

### Fixed
- N/A (Initial release)

### Security
- Non-root user in Docker (`botuser`)
- Health check endpoint for Railway
- Environment variable validation
- SQL injection prevention via parameterized queries

---

## [Unreleased]

### Planned
- [ ] Web dashboard for parlay tracking
- [ ] Mobile app (React Native)
- [ ] Webhook integrations with betting platforms
- [ ] Real-time push notifications
- [ ] Advanced ML models (LSTM, XGBoost)
- [ ] Cryptocurrency odds integration
- [ ] Historical odds database (1+ year)
- [ ] API endpoint for third-party integrations
