class PipelineRegistry:

    PIPELINES = [

        {
            "name": "ingestion",
            "module": "app.main",
            "depends_on": []
        },

        {
            "name": "features",
            "module": "app.features.feature_pipeline",
            "depends_on": [
                "ingestion"
            ]
        },

        {
            "name": "regime",
            "module": "app.regime.regime_pipeline",
            "depends_on": [
                "features"
            ]
        },

        {
            "name": "sector",
            "module": "app.sector.sector_pipeline",
            "depends_on": [
                "features",
                "regime"
            ]
        },

        {
            "name": "signals",
            "module": "app.signals.signal_pipeline",
            "depends_on": [
                "features",
                "sector",
                "regime"
            ]
        },

        {
            "name": "orchestration",
            "module": "app.orchestration.market_state_orchestrator",
            "depends_on": [
                "signals",
                "regime",
                "sector"
            ]
        }
    ]