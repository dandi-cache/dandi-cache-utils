"""What `update.py` and `refresh.py` share: assessing one content ID and recording the outcome.

The two entry points stream the same assets and run the same checks; they differ only in which
content IDs they select. This module is what is left of the repository's old `_pipeline_common.py`
once the JSONL loaders, the writers, the staged error logging and the remote NWB opening moved into
`dandi_cache_utils`.
"""

import datetime

import dandi_cache_utils as dandi_cache

VALIDITY = "content_id_to_valid_nwb_file.jsonl"
CHECKED_AT = "content_id_to_checked_at.jsonl"
MESSAGES = "content_id_to_messages.jsonl"

# Each phase of the work routes its failures to a dedicated log; anything raised outside a labelled
# phase lands in the catch-all `unexpected_errors.txt`.
STAGES = {
    "retrieving asset information from the DANDI API": "dandi_api_errors.txt",
    "opening and inspecting the NWB file": "nwb_inspector_errors.txt",
}


class Assessor:
    """Assesses one content ID at a time, accumulating the two side outputs as it goes."""

    def __init__(self, dataset, locations: dict) -> None:
        self.dataset = dataset
        self.locations = locations
        self.resolver = dandi_cache.api.AssetResolver()
        self.inspector_config = dandi_cache.nwb.inspector_config("dandi")
        self.checked_at = dataset.read_output_lookup(CHECKED_AT)
        self.messages = dataset.read_output_lookup(MESSAGES)

    def assess(self, content_id: str, item) -> bool:
        """Whether the asset passes the NWB Inspector at the CRITICAL threshold."""
        dandiset_id, path = dandi_cache.api.split_location(self.locations[content_id])

        item.stage = "retrieving asset information from the DANDI API"
        url = self.resolver.content_url(dandiset_id, path)

        item.stage = "opening and inspecting the NWB file"
        messages = dandi_cache.nwb.inspect_nwbfile(url, path, config=self.inspector_config)

        self.record(content_id, valid=not messages, detail={"messages": messages} if messages else None)
        return not messages

    def record(self, content_id: str, /, *, valid: bool, detail: dict | None) -> None:
        """Stamp the assessment date and keep the messages only while the file is not valid."""
        self.checked_at[content_id] = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
        if valid:
            self.messages.pop(content_id, None)
        elif detail is not None:
            self.messages[content_id] = detail

    def record_failure(self, content_id: str, item) -> None:
        """A content ID that could not be assessed is recorded as invalid, with why."""
        self.record(content_id, valid=False, detail={"error": item.error_summary})

    def write_side_outputs(self) -> None:
        """Write the two outputs that sit alongside the validity mapping."""
        self.dataset.write_output_lookup(self.checked_at, CHECKED_AT)
        self.dataset.write_output_lookup(self.messages, MESSAGES)
