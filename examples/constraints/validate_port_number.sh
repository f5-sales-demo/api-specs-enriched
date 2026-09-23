#!/bin/bash
# Example: Validate port number constraint
#
# Constraint (from x-f5xc-constraints):
# - type: number
# - minimum: 1
# - maximum: 65535
#
# Natural Language:
# Port numbers must be integers between 1 and 65535.

set -e

# F5 XC API credentials
: "${XCSH_API_URL:?XCSH_API_URL not set}"
: "${XCSH_API_TOKEN:?XCSH_API_TOKEN not set}"
: "${XCSH_TENANT:?XCSH_TENANT not set}"

# Test namespace
NAMESPACE="${XCSH_NAMESPACE:-test}"
API_BASE="${XCSH_API_URL}/api/config/namespaces/${NAMESPACE}"

echo "=========================================="
echo "Port Number Constraint Validation Tests"
echo "=========================================="
echo ""
echo "Testing against: ${API_BASE}/origin_pools"
echo ""

# Function to test a port number
test_port() {
  local port=$1
  local expected=$2 # "valid" or "invalid"
  local test_name="test-port-${port}-$$"

  echo "Testing port: ${port} (expecting: ${expected})"

  # Create origin pool with the test port
  response=$(curl -s -w "\n%{http_code}" -X POST "${API_BASE}/origin_pools" \
    -H "Authorization: APIToken ${XCSH_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
            \"metadata\": {
                \"name\": \"${test_name}\",
                \"namespace\": \"${NAMESPACE}\"
            },
            \"spec\": {
                \"origin_servers\": [
                    {
                        \"public_name\": {
                            \"dns_name\": \"example.com\"
                        }
                    }
                ],
                \"port\": ${port},
                \"no_tls\": {}
            }
        }" 2>&1)

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | head -n-1)

  if [ "$http_code" = "200" ] || [ "$http_code" = "409" ]; then
    echo "  ✅ Port accepted by API (HTTP ${http_code})"
    if [ "$expected" = "invalid" ]; then
      echo "  ⚠️  WARNING: Expected rejection but API accepted port ${port}!"
    fi

    # Clean up
    if [ "$http_code" = "200" ]; then
      curl -s -X DELETE "${API_BASE}/origin_pools/${test_name}" \
        -H "Authorization: APIToken ${XCSH_API_TOKEN}" >/dev/null
      echo "  🧹 Cleaned up test resource"
    fi
  elif [ "$http_code" = "400" ]; then
    echo "  ❌ Port rejected by API (HTTP 400)"
    echo "  Error: $(echo "$body" | jq -r '.message // .error // "Unknown error"' 2>/dev/null || echo "$body")"
    if [ "$expected" = "valid" ]; then
      echo "  ⚠️  WARNING: Expected acceptance but API rejected port ${port}!"
    fi
  else
    echo "  ⚠️  Unexpected HTTP ${http_code}"
    echo "  Response: $body"
  fi

  echo ""
}

# Test Cases

echo "1. Valid Port Numbers (should be accepted)"
echo "-------------------------------------------"
test_port 80 "valid"    # HTTP
test_port 443 "valid"   # HTTPS
test_port 8080 "valid"  # Alternative HTTP
test_port 22 "valid"    # SSH
test_port 1 "valid"     # Minimum
test_port 65535 "valid" # Maximum
test_port 3000 "valid"  # Common app port

echo "2. Invalid Port Numbers - Below minimum (should be rejected)"
echo "------------------------------------------------------------"
test_port 0 "invalid"
test_port -1 "invalid"
test_port -100 "invalid"

echo "3. Invalid Port Numbers - Above maximum (should be rejected)"
echo "------------------------------------------------------------"
test_port 65536 "invalid"
test_port 70000 "invalid"
test_port 100000 "invalid"

echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "✅ Valid ports: Should be accepted (HTTP 200/409)"
echo "❌ Invalid ports: Should be rejected (HTTP 400)"
echo ""
echo "Constraint Reference:"
echo "  Type: number (integer)"
echo "  Minimum: 1"
echo "  Maximum: 65535"
echo ""
