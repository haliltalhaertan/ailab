"""Query OSV for installed public PyPI package versions; never send local code."""
import importlib.metadata
import json
from pathlib import Path
from urllib.request import Request, urlopen


packages = sorted(
    {(dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions()
     if dist.metadata["Name"].lower().replace("_", "-") != "llm-lab"}
)
body = {"queries": [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
                    for name, version in packages]}
request = Request("https://api.osv.dev/v1/querybatch", data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
with urlopen(request, timeout=45) as response:
    result = json.load(response)
rows = result.get("results", [])
if len(rows) != len(packages):
    raise RuntimeError("Incomplete advisory response")
findings = [{"package": name, "version": version, **row}
            for (name, version), row in zip(packages, rows) if row]
report = {"source": "https://api.osv.dev/v1/querybatch", "package_count": len(packages),
          "packages_with_results": findings}
Path(__file__).with_name("dependency-advisories.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
