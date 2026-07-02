# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import sys
from google.adk import Context
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import AgentTool, McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from google.adk.workflow import node, START, Workflow, Edge
from google.adk.events import RequestInput
from mcp import StdioServerParameters

from app.config import config

logger = logging.getLogger("home_keeper_agent")

# Initialize MCP Toolset to connect to our local MCP Server
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        )
    )
)

# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------
class HomeKeeperState(BaseModel):
    # Security check logs
    security_logs: List[Dict[str, Any]] = Field(default_factory=list)
    # Chore database (mapping task name to assignee & status)
    chores: Dict[str, Any] = Field(
        default_factory=lambda: {
            "laundry": {"assignee": "Alice", "status": "pending"},
            "dishes": {"assignee": "Bob", "status": "completed"},
        }
    )
    # Appliance maintenance schedules and weather alerts
    maintenance_schedule: Dict[str, Any] = Field(
        default_factory=lambda: {
            "hvac_filter": {"last_replaced": "2026-05-01", "interval_days": 90},
            "gutters": {"last_checked": "2026-04-15", "interval_days": 180},
        }
    )
    # Temporary buffer for action requiring approval
    pending_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Sub-Agents definition
# ---------------------------------------------------------------------------
chore_manager_agent = LlmAgent(
    name="chore_manager_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are the Chore Manager Agent. "
        "Your role is to manage household chores. "
        "Use your mcp tools to view chores, assign them to family members, or update their status. "
        "Keep assignments structured and verify who is doing what."
    ),
    tools=[mcp_toolset],
)

maintenance_scheduler_agent = LlmAgent(
    name="maintenance_scheduler_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are the Maintenance Scheduler Agent. "
        "Use your mcp tools to monitor appliance filters (e.g. HVAC, water filters) and household maintenance dates. "
        "You suggest replacement schedules and check the weather using get_weather_forecast "
        "to suggest outdoor checks like clearing gutters. "
        "Keep schedules organized."
    ),
    tools=[mcp_toolset],
)

# ---------------------------------------------------------------------------
# Orchestrator Agent
# ---------------------------------------------------------------------------
home_keeper_orchestrator = LlmAgent(
    name="home_keeper_orchestrator",
    model=Gemini(model=config.model),
    instruction=(
        "You are the Home Keeper Orchestrator. "
        "Your job is to direct home maintenance and chore requests to the correct agent. "
        "- For chores, laundry, dish assignments, or cleaning schedules, delegate to chore_manager_agent. "
        "- For HVAC filters, gutters, maintenance dates, or appliance schedules, delegate to maintenance_scheduler_agent. "
        "After getting the sub-agent's response, summarize it clearly for the user."
    ),
    tools=[
        AgentTool(chore_manager_agent),
        AgentTool(maintenance_scheduler_agent),
    ],
)


# ---------------------------------------------------------------------------
# Workflow Function Nodes
# ---------------------------------------------------------------------------
@node
async def security_checkpoint(ctx: Context, node_input: str) -> str:
    """Performs security inspection on the user prompt (PII, Prompt Injection, Audit logging)."""
    import re
    import json
    
    # 1. PII Scrubbing (Regex for phone numbers, email addresses)
    phone_pattern = r"\b(?:\+?\d{1,3}[-.●]?)?\(?\d{3}\)?[-.●]?\d{3}[-.●]?\d{4}\b"
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    
    scrubbed_input = node_input
    pii_scrubbed = False
    
    if re.search(phone_pattern, scrubbed_input):
        scrubbed_input = re.sub(phone_pattern, "[REDACTED_PHONE]", scrubbed_input)
        pii_scrubbed = True
        
    if re.search(email_pattern, scrubbed_input):
        scrubbed_input = re.sub(email_pattern, "[REDACTED_EMAIL]", scrubbed_input)
        pii_scrubbed = True

    # 2. Prompt Injection detection
    injection_keywords = [
        "override instructions", 
        "ignore previous", 
        "prompt injection", 
        "system prompt",
        "bypass security",
        "you are now a bypass"
    ]
    injection_detected = False
    matched_keyword = None
    for keyword in injection_keywords:
        if keyword in scrubbed_input.lower():
            injection_detected = True
            matched_keyword = keyword
            break

    # 3. Domain-specific rule (restricted safety commands for Home Keeper)
    restricted_keywords = ["delete all chores", "wipe database", "reset all chores"]
    restricted_detected = False
    matched_restricted = None
    for kw in restricted_keywords:
        if kw in scrubbed_input.lower():
            restricted_detected = True
            matched_restricted = kw
            break

    # 4. Determine outcome & severity
    if injection_detected:
        severity = "CRITICAL"
        action_taken = "blocked_injection"
        ctx.route = "SECURITY_EVENT"
        output_msg = f"Security violation: potential prompt injection detected (keyword: '{matched_keyword}')."
    elif restricted_detected:
        severity = "WARNING"
        action_taken = "blocked_restricted_command"
        ctx.route = "SECURITY_EVENT"
        output_msg = f"Restricted operation: Command '{matched_restricted}' is not permitted without supervisor credentials."
    else:
        severity = "INFO"
        action_taken = "passed" if not pii_scrubbed else "scrubbed_pii"
        ctx.route = "ok"
        output_msg = scrubbed_input

    # 5. Structured JSON audit log
    audit_event = {
        "event_type": "security_inspection",
        "severity": severity,
        "pii_detected": pii_scrubbed,
        "prompt_injection_detected": injection_detected,
        "restricted_command_detected": restricted_detected,
        "action_taken": action_taken,
        "details": {
            "matched_keyword": matched_keyword or matched_restricted or "none",
            "original_length": len(node_input),
            "processed_length": len(scrubbed_input)
        }
    }
    
    # Write to local state for record-keeping safely
    logs = ctx.state.get("security_logs", [])
    logs.append(audit_event)
    ctx.state["security_logs"] = logs
    
    # Log structured JSON
    print(f"AUDIT_LOG: {json.dumps(audit_event)}")
    logger.info(f"AUDIT_LOG: {json.dumps(audit_event)}")
    
    return output_msg


@node
async def security_error_node(ctx: Context, node_input: str) -> str:
    """Terminal node for security violations."""
    return f"Access Denied: {node_input}"


@node(rerun_on_resume=True)
async def orchestrator_node(ctx: Context, node_input: str) -> str:
    """Executes the orchestrator agent and routes flow."""
    # Execute orchestrator
    result = await ctx.run_node(home_keeper_orchestrator, node_input=node_input)
    
    # Route based on whether high-priority approval is requested
    # Let's say roof checks or filter resets require human approval for demo purposes
    if "schedule roof check" in node_input.lower() or "reset HVAC replacement" in node_input.lower():
        ctx.route = "needs_approval"
        ctx.state["pending_action"] = node_input
        return result

    ctx.route = "done"
    return result


@node(rerun_on_resume=True)
async def approval_node(ctx: Context, node_input: str) -> str:
    """Interrupt node to ask for human approval before executing action."""
    interrupt_id = "maintenance_approval"
    
    # If resuming from human input
    if ctx.resume_inputs and interrupt_id in ctx.resume_inputs:
        user_response = ctx.resume_inputs[interrupt_id]
        logger.info(f"Received human approval response: {user_response}")
        
        pending = ctx.state.get("pending_action", "unknown action")
        if "yes" in user_response.lower() or "approve" in user_response.lower():
            maint = ctx.state.get("maintenance_schedule", {})
            maint["pending_approved"] = pending
            ctx.state["maintenance_schedule"] = maint
            ctx.state["pending_action"] = None
            return f"Action approved! Scheduled: '{pending}'"
        else:
            ctx.state["pending_action"] = None
            return "Action was rejected by user."

    # Return RequestInput to trigger interrupt
    logger.info("Triggering human-in-the-loop interrupt for approval.")
    return RequestInput(
        interrupt_id=interrupt_id,
        message=f"A home maintenance action requires approval: '{ctx.state.get('pending_action')}' - Do you approve? (yes/no)"
    )


@node
async def final_output(ctx: Context, node_input: str) -> str:
    """Final output display node."""
    return node_input


# ---------------------------------------------------------------------------
# Workflow Graph & App Definition
# ---------------------------------------------------------------------------
workflow = Workflow(
    name="home_keeper_workflow",
    description="Workflow to coordinate home chores, appliance schedules, and security verification.",
    state_schema=HomeKeeperState,
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"ok": orchestrator_node, "SECURITY_EVENT": security_error_node}),
        (orchestrator_node, {"needs_approval": approval_node, "done": final_output}),
        (approval_node, final_output),
        (security_error_node, final_output),
    ],
)

app = App(
    root_agent=workflow,
    name="app",
)
