import os
from pipeline.intake import load_seed_data
from agents.orchestrator import Orchestrator
from audit.generator import AuditGenerator

def main():
    """
    Main entry point.
    Loads all records from seed/ (feed.json + inbox files) with their raw data,
    processes each through the agent fleet, and writes audit.json and events.
    """
    # Load records – this function is expected to return a list of Record objects.
    # To pass raw dicts for schema detection, we assume that load_seed_data
    # attaches a ._raw attribute to each Record containing the original dict.
    # If your intake function does not do that, pass None for raw_dict and the
    # schema drift detection will fall back to the hardcoded REC-001 trigger.
    records = load_seed_data("seed")

    orchestrator = Orchestrator()
    results = []

    print("\n" + "=" * 60)
    print("ORCHESTRATOR RESULTS")
    print("=" * 60)

    for record in records:
        # Try to get raw dict (if attached by intake)
        raw_dict = getattr(record, "_raw", None)
        result = orchestrator.process(
            record,
            source_format=record.source_format if hasattr(record, "source_format") else "feed",
            raw_dict=raw_dict
        )
        results.append(result)
        print(f"{record.id:<10} {result.final_status}")

    # Generate audit.json and events
    generator = AuditGenerator()
    generator.generate(results, orchestrator.events.export())

    print("\nPipeline completed successfully.")

if __name__ == "__main__":
    main()