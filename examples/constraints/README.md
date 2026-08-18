# F5 XC API Constraint Validation Examples

**Purpose**: Demonstrate constraint validation using cURL and the live F5 XC API.

These scripts validate the constraints documented in `x-f5xc-constraints` extensions against the actual API behavior.

---

## Prerequisites

1. **F5 XC Account**: Access to F5 Distributed Cloud tenant
2. **API Token**: Generate from F5 XC Console → Account Settings → Credentials → API Tokens
3. **Test Namespace**: Create a test namespace (don't use production!)
4. **jq**: JSON command-line processor (`brew install jq` on macOS)

---

## Setup

Export your F5 XC credentials:

```bash
export F5XC_API_URL="https://your-tenant.console.ves.volterra.io"
export F5XC_API_TOKEN="your-api-token-here"
export F5XC_TENANT="your-tenant-name"
export F5XC_NAMESPACE="test"  # Use a test namespace!
```

**Security Note**: Never commit credentials to version control. Use environment variables or a secrets manager.

---

## Available Examples

### 1. DNS Label Validation (`validate_dns_label.sh`)

**Tests**: Resource name constraints (x-f5xc-constraints for `metadata.name`)

**Constraint**:

- Pattern: `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
- Length: 1-63 characters
- Format: dns-label (RFC 1123)

**Usage**:

```bash
chmod +x validate_dns_label.sh
./validate_dns_label.sh
```

**Test Cases**:

- ✅ Valid: `my-service`, `api-gateway`, `web`
- ❌ Invalid: `My-Service` (uppercase), `-my-service` (hyphen at start), `my_service` (underscore)

**Expected Behavior**:

- Valid names: HTTP 200 (created) or 409 (already exists)
- Invalid names: HTTP 400 (bad request) with validation error message

---

### 2. Port Number Validation (`validate_port_number.sh`)

**Tests**: Port number constraints (x-f5xc-constraints for `spec.port`)

**Constraint**:

- Type: number (integer)
- Minimum: 1
- Maximum: 65535

**Usage**:

```bash
chmod +x validate_port_number.sh
./validate_port_number.sh
```

**Test Cases**:

- ✅ Valid: `80`, `443`, `8080`, `1`, `65535`
- ❌ Invalid: `0`, `-1`, `65536`, `100000`

**Expected Behavior**:

- Valid ports: HTTP 200 (created) or 409 (already exists)
- Invalid ports: HTTP 400 with validation error

---

### 3. Array Size Validation (`validate_array_size.sh`)

**Tests**: Array constraints (x-f5xc-constraints for arrays)

**Constraint**:

- Type: array
- minItems: 1
- maxItems: 50

**Usage**:

```bash
chmod +x validate_array_size.sh
./validate_array_size.sh
```

**Test Cases**:

- ✅ Valid: 1 item, 5 items, 50 items
- ❌ Invalid: 0 items (empty array)

**Expected Behavior**:

- Valid sizes: HTTP 200 (created)
- Invalid sizes: HTTP 400 with validation error

---

## Understanding Test Results

### Success Indicators

✅ **Name format accepted** (HTTP 200/409)

- HTTP 200: Resource created successfully
- HTTP 409: Resource already exists (name format valid)
- The constraint allows this value

### Failure Indicators

❌ **Name format rejected** (HTTP 400)

- HTTP 400: Bad request - constraint violation
- The API validation matches the documented constraint

### Warnings

⚠️ **Unexpected behavior**

- Expected valid but got HTTP 400: Constraint may be incorrect or incomplete
- Expected invalid but got HTTP 200/409: Constraint may be too permissive

---

## Validating Constraints

These scripts help verify:

1. **Constraint Accuracy**: Do documented constraints match API behavior?
2. **Pattern Correctness**: Does the regular expression pattern accurately describe valid values?
3. **Confidence Scores**: Are high-confidence (deterministic) constraints reliable?
4. **Discovery Integration**: Does discovery data improve constraint accuracy?

---

## Automated Validation

Run all validation scripts:

```bash
#!/bin/bash
# run_all_validations.sh

for script in validate_*.sh; do
    echo "=========================================="
    echo "Running: $script"
    echo "=========================================="
    ./"$script"
    echo ""
    echo ""
done
```

---

## Interpreting Results

### High Match Rate (>95%)

- Constraints are highly accurate
- Safe for AI generation with `deterministic: true`
- Recommend using for CLI validation

### Medium Match Rate (80-95%)

- Constraints are mostly accurate
- Use for advisory hints, not strict enforcement
- May need refinement from discovery data

### Low Match Rate (<80%)

- Constraints may be incorrect or incomplete
- Do not use for automated generation
- Report issue for investigation

---

## Reporting Issues

If you find constraint mismatches:

1. **Note the field path**: e.g., `metadata.name`, `spec.port`
2. **Document the test case**: What value was tested?
3. **Include API response**: What did the API return?
4. **Report at**: [GitHub Issues](https://github.com/f5-sales-demo/api-specs-enriched/issues)

**Issue Template**:

```markdown
**Constraint Mismatch**

Field: `metadata.name`
Documented Constraint: `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
Test Value: `My-Service`
Expected: Rejection (HTTP 400)
Actual: Acceptance (HTTP 200)

API Response:
{
  "status": "success",
  "id": "..."
}
```

---

## Best Practices

1. **Use Test Namespaces**: Never run validation scripts against production namespaces
2. **Clean Up Resources**: Scripts auto-clean created resources, but verify manually
3. **Rate Limiting**: Be mindful of API rate limits; add delays between tests if needed
4. **API Token Security**: Rotate tokens regularly, never commit to version control
5. **Monitor Costs**: Some resources may incur charges; delete test resources promptly

---

## Advanced Usage

### Custom Constraints

Test your own constraints:

```bash
# Test custom pattern
curl -X POST "${F5XC_API_URL}/api/config/namespaces/default/http_loadbalancers" \
    -H "Authorization: APIToken ${F5XC_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
        "metadata": {
            "name": "your-test-value",
            "namespace": "default"
        },
        "spec": {}
    }'
```

### Batch Validation

Validate multiple values:

```bash
# Test multiple DNS labels
for name in "valid-name" "Invalid-Name" "-invalid"; do
    echo "Testing: $name"
    # ... curl request ...
done
```

---

## Resources

- **Constraint Metadata Documentation**: [docs/CONSTRAINT_METADATA.md](../../docs/CONSTRAINT_METADATA.md)
- **F5 XC API Documentation**: [F5 XC API Documentation](https://docs.cloud.f5.com/docs/api)
- **OpenAPI Specifications**: [docs/specifications/api/](../../docs/specifications/api/)

---

**Last Updated**: 2026-01-19
**Version**: 3.3.0
