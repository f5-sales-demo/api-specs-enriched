#!/bin/bash
# Example: Validate DNS label constraint for resource names
#
# Constraint (from x-f5xc-constraints):
# - minLength: 1
# - maxLength: 63
# - pattern: ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$
# - format: dns-label (RFC 1123)
#
# Natural Language:
# DNS label names must be 1-63 lowercase alphanumeric characters.
# Hyphens allowed but not at start/end.

set -e

# F5 XC API credentials (set these environment variables)
: "${XCSH_API_URL:?XCSH_API_URL not set}"
: "${XCSH_API_TOKEN:?XCSH_API_TOKEN not set}"
: "${XCSH_TENANT:?XCSH_TENANT not set}"

# Test namespace (use a test namespace, not production!)
NAMESPACE="${XCSH_NAMESPACE:-test}"

# Base API endpoint
API_BASE="${XCSH_API_URL}/api/config/namespaces/${NAMESPACE}"

echo "=========================================="
echo "DNS Label Constraint Validation Tests"
echo "=========================================="
echo ""
echo "Testing against: ${API_BASE}/http_loadbalancers"
echo ""

# Function to test a resource name
test_name() {
  local name=$1
  local expected=$2 # "valid" or "invalid"

  echo "Testing: '${name}' (expecting: ${expected})"

  # Create HTTP load balancer with the test name
  response=$(curl -s -w "\n%{http_code}" -X POST "${API_BASE}/http_loadbalancers" \
    -H "Authorization: APIToken ${XCSH_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
            \"metadata\": {
                \"name\": \"${name}\",
                \"namespace\": \"${NAMESPACE}\"
            },
            \"spec\": {}
        }" 2>&1)

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | head -n-1)

  if [ "$http_code" = "200" ] || [ "$http_code" = "409" ]; then
    # 200 = created, 409 = already exists (both indicate name format is valid)
    echo "  ✅ Name format accepted by API (HTTP ${http_code})"
    if [ "$expected" = "invalid" ]; then
      echo "  ⚠️  WARNING: Expected rejection but API accepted it!"
    fi

    # Clean up if we created it
    if [ "$http_code" = "200" ]; then
      curl -s -X DELETE "${API_BASE}/http_loadbalancers/${name}" \
        -H "Authorization: APIToken ${XCSH_API_TOKEN}" >/dev/null
      echo "  🧹 Cleaned up test resource"
    fi
  elif [ "$http_code" = "400" ]; then
    echo "  ❌ Name format rejected by API (HTTP 400)"
    echo "  Error: $(echo "$body" | jq -r '.message // .error // "Unknown error"' 2>/dev/null || echo "$body")"
    if [ "$expected" = "valid" ]; then
      echo "  ⚠️  WARNING: Expected acceptance but API rejected it!"
    fi
  else
    echo "  ⚠️  Unexpected HTTP ${http_code}"
    echo "  Response: $body"
  fi

  echo ""
}

# Test Cases

echo "1. Valid DNS Labels (should be accepted)"
echo "----------------------------------------"
test_name "my-service" "valid"
test_name "api-gateway" "valid"
test_name "lb-prod-01" "valid"
test_name "web" "valid"
test_name "a" "valid"
test_name "api-gateway-production-us-west-2-lb-01" "valid" # 38 chars

echo "2. Invalid DNS Labels - Uppercase (should be rejected)"
echo "------------------------------------------------------"
test_name "My-Service" "invalid"
test_name "API-GATEWAY" "invalid"

echo "3. Invalid DNS Labels - Hyphens at edges (should be rejected)"
echo "------------------------------------------------------------"
test_name "-my-service" "invalid"
test_name "my-service-" "invalid"
test_name "-" "invalid"

echo "4. Invalid DNS Labels - Special characters (should be rejected)"
echo "---------------------------------------------------------------"
test_name "my_service" "invalid"
test_name "my.service" "invalid"
test_name "my service" "invalid"
test_name "my@service" "invalid"

echo "5. Invalid DNS Labels - Length violations (should be rejected)"
echo "--------------------------------------------------------------"
test_name "" "invalid"                                                                              # Too short
test_name "this-is-a-very-long-name-that-exceeds-the-sixty-three-character-maximum-limit" "invalid" # Too long (79 chars)

echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "✅ Valid names: Should be accepted (HTTP 200/409)"
echo "❌ Invalid names: Should be rejected (HTTP 400)"
echo ""
echo "Constraint Reference:"
echo "  Pattern: ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
echo "  Length: 1-63 characters"
echo "  Format: dns-label (RFC 1123)"
echo ""
