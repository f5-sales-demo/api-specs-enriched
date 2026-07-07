# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for GuidedWorkflowEnricher."""

import pytest

from scripts.utils.guided_workflow_enricher import (
    GuidedWorkflow,
    GuidedWorkflowEnricher,
    GuidedWorkflowEnrichmentStats,
    WorkflowStep,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return GuidedWorkflowEnricher()


@pytest.fixture
def spec_with_virtual_domain():
    """Create a spec with virtual domain classification."""
    return {
        "info": {
            "title": "F5 XC Virtual API",
            "description": "Virtual load balancing API",
            "x-f5xc-cli-domain": "virtual",
        },
        "paths": {},
    }


@pytest.fixture
def spec_without_domain():
    """Create a spec without domain classification."""
    return {
        "info": {
            "title": "Unknown API",
            "description": "Some API",
        },
        "paths": {},
    }


class TestGuidedWorkflowEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes correctly."""
        enricher = GuidedWorkflowEnricher()
        assert enricher is not None
        assert enricher.config_path is not None

    def test_config_version(self, enricher):
        """Test config version is loaded."""
        version = enricher.get_config_version()
        assert version is not None
        assert isinstance(version, str)

    def test_configured_domains(self, enricher):
        """Test configured domains list."""
        domains = enricher.get_configured_domains()
        assert isinstance(domains, list)
        assert "virtual" in domains

    def test_stats_initialization(self, enricher):
        """Test stats are initialized correctly."""
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 0
        assert stats["workflows_applied"] == 0
        assert stats["workflows_skipped"] == 0


class TestWorkflowRetrieval:
    """Test workflow retrieval methods."""

    def test_get_workflows_for_virtual(self, enricher):
        """Test getting workflows for virtual domain."""
        workflows = enricher.get_workflows("virtual")
        assert isinstance(workflows, list)
        assert len(workflows) > 0

    def test_get_workflows_unknown_domain(self, enricher):
        """Test getting workflows for unknown domain returns empty."""
        workflows = enricher.get_workflows("unknown_domain_xyz")
        assert isinstance(workflows, list)
        assert len(workflows) == 0

    def test_workflow_has_required_fields(self, enricher):
        """Test workflows have required fields."""
        workflows = enricher.get_workflows("virtual")
        assert len(workflows) > 0

        workflow = workflows[0]
        assert isinstance(workflow, GuidedWorkflow)
        assert hasattr(workflow, "id")
        assert hasattr(workflow, "name")
        assert hasattr(workflow, "description")
        assert hasattr(workflow, "steps")

    def test_has_workflows(self, enricher):
        """Test has_workflows method."""
        assert enricher.has_workflows("virtual") is True
        assert enricher.has_workflows("unknown_domain_xyz") is False

    def test_get_workflow_by_id(self, enricher):
        """Test getting a specific workflow by ID."""
        workflows = enricher.get_workflows("virtual")
        if len(workflows) > 0:
            workflow_id = workflows[0].id
            workflow = enricher.get_workflow("virtual", workflow_id)
            assert workflow is not None
            assert workflow.id == workflow_id


class TestWorkflowStepStructure:
    """Test WorkflowStep dataclass structure."""

    def test_workflow_has_steps(self, enricher):
        """Test workflows contain steps."""
        workflows = enricher.get_workflows("virtual")
        assert len(workflows) > 0

        workflow = workflows[0]
        assert isinstance(workflow.steps, list)
        assert len(workflow.steps) > 0

    def test_step_has_required_fields(self, enricher):
        """Test workflow steps have required fields."""
        workflows = enricher.get_workflows("virtual")
        workflow = workflows[0]
        step = workflow.steps[0]

        assert isinstance(step, WorkflowStep)
        assert hasattr(step, "order")
        assert hasattr(step, "action")
        assert hasattr(step, "name")
        assert hasattr(step, "description")

    def test_step_to_dict(self, enricher):
        """Test step to_dict method."""
        workflows = enricher.get_workflows("virtual")
        workflow = workflows[0]
        step = workflow.steps[0]

        step_dict = step.to_dict()
        assert isinstance(step_dict, dict)
        assert "order" in step_dict
        assert "action" in step_dict


class TestSpecEnrichment:
    """Test spec enrichment functionality."""

    def test_enrich_spec_with_domain(self, enricher, spec_with_virtual_domain):
        """Test enriching spec with domain classification."""
        enriched = enricher.enrich_spec(spec_with_virtual_domain, domain="virtual")
        assert "info" in enriched
        assert "x-f5xc-guided-workflows" in enriched["info"]

    def test_enrich_spec_uses_spec_domain(self, enricher, spec_with_virtual_domain):
        """Test enriching spec uses domain from spec."""
        enriched = enricher.enrich_spec(spec_with_virtual_domain)
        assert "info" in enriched
        assert "x-f5xc-guided-workflows" in enriched["info"]

    def test_enrich_spec_without_domain(self, enricher, spec_without_domain):
        """Test enriching spec without domain does not add workflows."""
        enriched = enricher.enrich_spec(spec_without_domain)
        assert "x-f5xc-guided-workflows" not in enriched.get("info", {})

    def test_enriched_workflows_structure(self, enricher, spec_with_virtual_domain):
        """Test enriched workflows have correct structure."""
        enriched = enricher.enrich_spec(spec_with_virtual_domain, domain="virtual")
        workflows = enriched["info"]["x-f5xc-guided-workflows"]

        assert isinstance(workflows, list)
        assert len(workflows) > 0

        workflow = workflows[0]
        assert "id" in workflow
        assert "name" in workflow
        assert "steps" in workflow

    def test_enrich_spec_stats_updated(self, enricher, spec_with_virtual_domain):
        """Test stats are updated after enrichment."""
        enricher.enrich_spec(spec_with_virtual_domain, domain="virtual")
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 1
        assert stats["workflows_applied"] > 0


class TestGuidedWorkflowEnrichmentStats:
    """Test enrichment statistics dataclass."""

    def test_stats_initialization(self):
        """Test stats initialize with zeros."""
        stats = GuidedWorkflowEnrichmentStats()
        assert stats.specs_processed == 0
        assert stats.workflows_applied == 0
        assert stats.workflows_skipped == 0
        assert stats.total_workflows == 0

    def test_stats_to_dict(self):
        """Test stats to_dict method."""
        stats = GuidedWorkflowEnrichmentStats(
            specs_processed=5,
            workflows_applied=10,
            workflows_skipped=2,
            total_workflows=12,
        )
        stats_dict = stats.to_dict()
        assert isinstance(stats_dict, dict)
        assert stats_dict["specs_processed"] == 5
        assert stats_dict["workflows_applied"] == 10


class TestWorkflowToDict:
    """Test workflow to_dict output."""

    def test_workflow_to_dict(self, enricher):
        """Test workflow to_dict method."""
        workflows = enricher.get_workflows("virtual")
        assert len(workflows) > 0

        workflow = workflows[0]
        workflow_dict = workflow.to_dict()

        assert isinstance(workflow_dict, dict)
        assert "id" in workflow_dict
        assert "name" in workflow_dict
        assert "description" in workflow_dict
        assert "steps" in workflow_dict

    def test_workflow_steps_in_dict(self, enricher):
        """Test that steps are included in workflow dict."""
        workflows = enricher.get_workflows("virtual")
        workflow = workflows[0]
        workflow_dict = workflow.to_dict()

        steps = workflow_dict["steps"]
        assert isinstance(steps, list)
        if len(steps) > 0:
            step = steps[0]
            assert "order" in step
            assert "action" in step


@pytest.fixture
def spec_with_authentication_domain():
    """Create a spec classified under the authentication CLI domain."""
    return {
        "info": {
            "title": "F5 XC Authentication API",
            "description": "Authentication and API credential management",
            "x-f5xc-cli-domain": "authentication",
        },
        "paths": {},
    }


class TestAuthenticationApiTokenWorkflow:
    """The authentication domain must ship a create-and-use-API-token roadmap.

    This encodes the deterministic roadmap the model follows: create an API
    token via guided browser automation in the console, capture the one-time
    revealed token, then call the F5 XC API with it via the XC-API tool.
    """

    WORKFLOW_ID = "create_and_use_api_token"

    def test_authentication_is_a_configured_domain(self, enricher):
        """authentication must be present as a guided-workflow domain."""
        assert "authentication" in enricher.get_configured_domains()

    def test_workflow_present_with_three_ordered_steps(self, enricher):
        """The workflow exists and has the three expected ordered steps."""
        workflow = enricher.get_workflow("authentication", self.WORKFLOW_ID)
        assert workflow is not None, f"missing workflow {self.WORKFLOW_ID}"

        actions = [step.action for step in workflow.steps]
        assert actions == [
            "create_api_token",
            "capture_token",
            "call_api_with_token",
        ]
        orders = [step.order for step in workflow.steps]
        assert orders == sorted(orders), "steps must be ordered"

    def test_step_dependencies_and_create_step_shape(self, enricher):
        """Later steps depend on earlier ones; the create step targets api_credential."""
        workflow = enricher.get_workflow("authentication", self.WORKFLOW_ID)
        assert workflow is not None
        by_action = {step.action: step for step in workflow.steps}

        create = by_action["create_api_token"]
        assert create.resource == "api_credential"
        assert "credential_type" in create.required_fields

        assert by_action["capture_token"].depends_on == [1]
        assert by_action["call_api_with_token"].depends_on == [2]

    def test_workflow_injected_into_authentication_spec(
        self, enricher, spec_with_authentication_domain
    ):
        """The workflow is emitted under x-f5xc-guided-workflows for the domain."""
        enriched = enricher.enrich_spec(
            spec_with_authentication_domain, domain="authentication"
        )
        workflows = enriched["info"]["x-f5xc-guided-workflows"]
        assert any(w["id"] == self.WORKFLOW_ID for w in workflows)
