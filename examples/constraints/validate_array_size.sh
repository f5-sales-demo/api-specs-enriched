#!/bin/bash
# Example: Validate array size constraint for origin servers
#
# Constraint (from x-f5xc-constraints):
# - type: array
# - minItems: 1
# - maxItems: 50
# - uniqueItems: false (for origin_servers)
#
# Natural Language:
# Array of origin servers requires at least 1 item, maximum 50 items.

set -e

# F5 XC API credentials
: "${XCSH_API_URL:?XCSH_API_URL not set}"
: "${XCSH_API_TOKEN:?XCSH_API_TOKEN not set}"
: "${XCSH_TENANT:?XCSH_TENANT not set}"

# Test namespace
NAMESPACE="${XCSH_NAMESPACE:-test}"
API_BASE="${XCSH_API_URL}/api/config/namespaces/${NAMESPACE}"

echo "=========================================="
echo "Array Size Constraint Validation Tests"
echo "=========================================="
echo ""
echo "Testing against: ${API_BASE}/origin_pools"
echo "Testing: origin_servers array size"
echo ""

# Function to generate origin servers array
generate_origins() {
  local count=$1
  local origins="[]"

  if [ "$count" -gt 0 ]; then
    origins="["
    for i in $(seq 1 "$count"); do
      if [ "$i" -gt 1 ]; then
        origins="${origins},"
      fi
      origins="${origins}{\"public_name\":{\"dns_name\":\"origin${i}.example.com\"}}"
    done
    origins="${origins}]"
  fi

  echo "$origins"
}

# Function to test array size
test_array_size() {
  local size=$1
  local expected=$2 # "valid" or "invalid"
  local test_name="test-array-${size}-$$"

  echo "Testing array with ${size} items (expecting: ${expected})"

  origins=$(generate_origins "$size")

  response=$(curl -s -w "\n%{http_code}" -X POST "${API_BASE}/origin_pools" \
    -H "Authorization: APIToken ${XCSH_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
            \"metadata\": {
                \"name\": \"${test_name}\",
                \"namespace\": \"${NAMESPACE}\"
            },
            \"spec\": {
                \"origin_servers\": ${origins},
                \"port\": 443,
                \"no_tls\": {}
            }
        }" 2>&1)

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | head -n-1)

  if [ "$http_code" = "200" ] || [ "$http_code" = "409" ]; then
    echo "  ✅ Array size accepted by API (HTTP ${http_code})"
    if [ "$expected" = "invalid" ]; then
      echo "  ⚠️  WARNING: Expected rejection but API accepted ${size} items!"
    fi

    # Clean up
    if [ "$http_code" = "200" ]; then
      curl -s -X DELETE "${API_BASE}/origin_pools/${test_name}" \
        -H "Authorization: APIToken ${XCSH_API_TOKEN}" >/dev/null
      echo "  🧹 Cleaned up test resource"
    fi
  elif [ "$http_code" = "400" ]; then
    echo "  ❌ Array size rejected by API (HTTP 400)"
    echo "  Error: $(echo "$body" | jq -r '.message // .error // "Unknown error"' 2>/dev/null || echo "$body")"
    if [ "$expected" = "valid" ]; then
      echo "  ⚠️  WARNING: Expected acceptance but API rejected ${size} items!"
    fi
  else
    echo "  ⚠️  Unexpected HTTP ${http_code}"
    echo "  Response: $(echo "$body" | head -c 200)"
  fi

  echo ""
}

# Test Cases

echo "1. Valid Array Sizes (should be accepted)"
echo "------------------------------------------"
test_array_size 1 "valid"  # Minimum
test_array_size 2 "valid"  # Typical
test_array_size 5 "valid"  # Multiple origins
test_array_size 10 "valid" # Larger pool
test_array_size 50 "valid" # Maximum

echo "2. Invalid Array Sizes - Below minimum (should be rejected)"
echo "-----------------------------------------------------------"
test_array_size 0 "invalid" # Empty array

echo "3. Invalid Array Sizes - Above maximum (should be rejected)"
echo "-----------------------------------------------------------"
# Note: Creating 51+ origin servers may take time and hit API limits
echo "  ⚠️  Skipping >50 item tests to avoid API rate limits"
echo "  In practice, arrays >50 items should be rejected with HTTP 400"
echo ""

echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "✅ Valid array sizes: Should be accepted (HTTP 200/409)"
echo "❌ Invalid array sizes: Should be rejected (HTTP 400)"
echo ""
echo "Constraint Reference:"
echo "  Type: array"
echo "  minItems: 1"
echo "  maxItems: 50"
echo "  uniqueItems: false (duplicates allowed)"
echo ""
