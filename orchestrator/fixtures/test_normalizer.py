import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scratch", "phase1", "proof_happy_path"))

import result

assert result.normalize_company_name("  ACME GmbH  ") == "acme gmbh"
assert result.normalize_company_name("Company-Outreach") == "company outreach"
assert result.normalize_company_name("Enterprise__System") == "enterprise system"
assert result.normalize_company_name("A   B") == "a b"

print("normalize_company_name: OK")
