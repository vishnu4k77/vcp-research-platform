from app.services.market_query_service import (
    MarketQueryService
)

from app.services.scanner_service import (
    ScannerService
)


df = MarketQueryService.get_stock_data(
    "RELIANCE.NS"
)

result = ScannerService.is_above_50ma(df)

print(result)