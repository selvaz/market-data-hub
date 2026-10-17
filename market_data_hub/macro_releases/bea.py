"""BEA NIPA percent changes. A missing line ALWAYS fails, never widens selection."""
from __future__ import annotations

from datetime import date, datetime
import re

from .adapters import Observation
from .http import FetchError, api_key, json_payload

ENDPOINT = "https://apps.bea.gov/api/data/"
TABLE = "T10101"
EDITIONS = {"2026-07-30": ("2026-Q2", "advance"),
            "2026-08-26": ("2026-Q2", "second"), "2026-09-30": ("2026-Q2", "third")}
ARCHIVES = {
    "2026-07-30": "https://www.bea.gov/sites/default/files/2026-07/gdp2q26-adv.pdf",
    "2026-08-26": "https://www.bea.gov/sites/default/files/2026-08/gdp2q26-2nd.pdf",
}


class BEAAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        if "us_gdp" not in families:
            return []
        years = ",".join(str(y) for y in range(min(int(since[:4]), date.today().year - 5), date.today().year + 1))
        doc = self.client.get("bea", ENDPOINT, {"UserID": api_key("BEA_API_KEY"),
            "method": "GetData", "datasetname": "NIPA", "TableName": TABLE,
            "Frequency": "Q", "Year": years, "ResultFormat": "JSON"})
        out = self.parse(doc, since=since)
        for edition, url in ARCHIVES.items():
            period, _ = EDITIONS[edition]
            if f"{period[:4]}-{int(period[-1]) * 3:02d}" >= since:
                archived = self.client.get("bea", url)
                out.extend(self.parse_archived_release(archived))
        return out

    @staticmethod
    def parse(document, *, since="1900-01", line="1"):
        payload = json_payload(document).get("BEAAPI", {})
        result = payload.get("Results", {})
        if "Error" in result or "Error" in payload:
            raise FetchError("BEA API error")
        rows = [r for r in result.get("Data", []) if str(r.get("LineNumber")) == str(line)
                and r.get("TableName") == TABLE]
        if not rows:
            raise FetchError(f"BEA requested line absent: {TABLE}/{line}")
        if str(line) != "1" or any(r.get("SeriesCode") != "A191RL" or
                                   r.get("CL_UNIT") != "Percent change, annual rate" or
                                   str(r.get("UNIT_MULT")) != "0" for r in rows):
            raise FetchError("BEA requested measure is not real GDP q/q saar")
        note = " ".join(n["NoteText"] for n in result.get("Notes", []) if n.get("NoteRef") == TABLE)
        match = re.search(r"LastRevised:\s*(\w+ \d+, \d{4})", note)
        if not match:
            raise FetchError("BEA table lacks issuer revision date")
        edition = datetime.strptime(match[1], "%B %d, %Y").date().isoformat()
        if edition not in EDITIONS:
            raise FetchError(f"BEA edition/stage metadata needs review: {edition}")
        headline, stage = EDITIONS[edition]
        out = []
        for row in rows:
            if not re.fullmatch(r"\d{4}Q[1-4]", row["TimePeriod"]):
                raise FetchError("BEA unexpected frequency")
            period = row["TimePeriod"].replace("Q", "-Q")
            end = f"{period[:4]}-{int(period[-1]) * 3:02d}"
            if end < since:
                continue
            printed = row["DataValue"]
            out.append(Observation("us_gdp_qq_saar", "us_gdp", period,
                                   float(printed.replace(",", "")), printed,
                                   TABLE + ":1:A191RL", "BEA", document, edition,
                                   edition + "T12:30:00Z", stage=stage if period == headline else "regular",
                                   revision="initial" if stage == "advance" and period == headline else "routine",
                                   precision="exact", provider_vintage=edition, event_stage=stage))
        return out

    @staticmethod
    def parse_archived_release(document):
        """Issuer archives certify old estimates which the current NIPA API replaces."""
        from io import BytesIO
        from pypdf import PdfReader

        if not document.body.startswith(b"%PDF"):
            raise FetchError("BEA archived estimate requires issuer PDF")
        text = re.sub(r"\s+", " ", PdfReader(BytesIO(document.body)).pages[0].extract_text())
        date_match = re.search(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday), (\w+ \d+, \d{4})", text)
        value = re.search(r"Real gross domestic product \(GDP\) (increased|decreased) at an annual rate of "
                          r"([\d.]+) percent in the (first|second|third|fourth) quarter of (\d{4})", text)
        if date_match is None or value is None:
            raise FetchError("BEA archived release format changed")
        edition = datetime.strptime(date_match[1], "%B %d, %Y").date().isoformat()
        period = value[4] + "-Q" + str(("first", "second", "third", "fourth").index(value[3]) + 1)
        if edition not in EDITIONS or EDITIONS[edition][0] != period:
            raise FetchError("BEA archived edition needs review")
        stage = EDITIONS[edition][1]
        return [Observation("us_gdp_qq_saar", "us_gdp", period,
                            float(value[2]) * (-1 if value[1] == "decreased" else 1), value[2],
                            "GDP:archived-published-rate", "BEA", document, edition,
                            edition + "T12:30:00Z", stage=stage, precision="exact", method="pdf",
                            revision="initial" if stage == "advance" else "routine")]
