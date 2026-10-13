import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    mode: str = "demo"
    model: str = ""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    data_dir: Path = field(default_factory=lambda: Path(".casedesk"))
    max_model_calls: int = 6

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.mode not in {"demo", "live"}:
            raise ValueError("CASEDESK_MODE must be demo or live")
        if self.mode == "live" and (not self.model.strip() or not self.api_key):
            raise ValueError("Live mode requires CASEDESK_MODEL and CASEDESK_API_KEY")
        if type(self.max_model_calls) is not int or not 1 <= self.max_model_calls <= 10:
            raise ValueError("max_model_calls must be 1 to 10")

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.getenv("CASEDESK_MODE", "demo"),
            model=os.getenv("CASEDESK_MODEL", ""),
            api_key=os.getenv("CASEDESK_API_KEY", ""),
            base_url=os.getenv("CASEDESK_BASE_URL", "https://api.openai.com/v1"),
            data_dir=Path(os.getenv("CASEDESK_DATA_DIR", ".casedesk")),
        )
