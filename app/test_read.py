from app.services.market_query_service import (
    MarketQueryService
)


df = MarketQueryService.get_stock_data(
    "RELIANCE.NS"
)

print(df.head())

print(df.shape)