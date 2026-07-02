import os
import json
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

# Define the DB file path
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "home_keeper_db.json")

# Helper to load and save DB
def load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_FILE):
        default_db = {
            "chores": {
                "laundry": {"assignee": "Alice", "status": "pending"},
                "dishes": {"assignee": "Bob", "status": "completed"},
                "mowing": {"assignee": "John", "status": "pending"},
            },
            "maintenance": {
                "hvac_filter": {"last_replaced": "2026-05-01", "interval_days": 90},
                "gutters": {"last_checked": "2026-04-15", "interval_days": 180},
            }
        }
        with open(DB_FILE, "w") as f:
            json.dump(default_db, f, indent=4)
        return default_db
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        # Fallback if file corrupt
        return {}

def save_db(db: Dict[str, Any]):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)

# Create the FastMCP server
mcp = FastMCP("HomeKeeper MCP Server")

@mcp.tool()
def get_chore_list() -> str:
    """Retrieves the list of household chores with their assignees and completion status.
    
    Returns:
        A text summary of all chores and their current status.
    """
    db = load_db()
    chores = db.get("chores", {})
    if not chores:
        return "No chores found."
    
    lines = ["Current Household Chores:"]
    for task, info in chores.items():
        lines.append(f"- {task}: Assigned to {info.get('assignee') or 'Unassigned'} [{info.get('status')}]")
    return "\n".join(lines)


@mcp.tool()
def assign_chore(task: str, assignee: str) -> str:
    """Assigns a specific household chore to a family member.
    
    Args:
        task: The name of the chore to assign (e.g. laundry, mowing).
        assignee: The name of the person being assigned.
        
    Returns:
        A confirmation message.
    """
    db = load_db()
    chores = db.setdefault("chores", {})
    
    if task not in chores:
        chores[task] = {"assignee": assignee, "status": "pending"}
    else:
        chores[task]["assignee"] = assignee
        chores[task]["status"] = "pending"
        
    save_db(db)
    return f"Chore '{task}' successfully assigned to {assignee}."


@mcp.tool()
def get_weather_forecast(city: str) -> str:
    """Retrieves the weather forecast for a city to evaluate necessary storm or outdoor checkups.
    
    Args:
        city: The name of the city to get the forecast for.
        
    Returns:
        A weather forecast status description.
    """
    city_lower = city.lower()
    if "sf" in city_lower or "san francisco" in city_lower:
        return "Weather for San Francisco: Heavy Storm and high wind warning forecast for tomorrow morning."
    elif "seattle" in city_lower:
        return "Weather for Seattle: Light drizzle, cool temperature (50 degrees)."
    return f"Weather for {city}: Clear sky and sunny, perfect for outdoor chores."


@mcp.tool()
def get_maintenance_log() -> str:
    """Retrieves the maintenance schedule log for home appliances and structures.
    
    Returns:
        A list of home structures and appliances with their replacement/check cycles.
    """
    db = load_db()
    maint = db.get("maintenance", {})
    if not maint:
        return "No maintenance log entries."
    
    lines = ["Home Maintenance Logs:"]
    for item, info in maint.items():
        lines.append(f"- {item}: Last checked/replaced on {info.get('last_replaced')} (every {info.get('interval_days')} days)")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
