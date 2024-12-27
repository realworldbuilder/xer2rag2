import streamlit as st
import pandas as pd
from xer_reader import XerReader
from datetime import datetime
from pathlib import Path
import json
import tempfile

class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime objects"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%d %H:%M')
        return super().default(obj)

def interpret_constraint(cstr_type):
    """Convert constraint type code to readable description"""
    constraints = {
        "CS_MSO": "Must Start On",
        "CS_MSOB": "Must Start On or Before",
        "CS_MSOA": "Must Start On or After",
        "CS_MEO": "Must End On",
        "CS_MEOB": "Must End On or Before",
        "CS_MEOA": "Must End On or After",
        "CS_ALAP": "As Late As Possible",
        "CS_ASAP": "As Soon As Possible"
    }
    return constraints.get(cstr_type, "No constraint")

def interpret_task_type(task_type):
    """Convert task type code to readable description"""
    types = {
        "TT_Task": "Normal Task",
        "TT_Rsrc": "Resource Dependent",
        "TT_LOE": "Level of Effort",
        "TT_Mile": "Milestone",
        "TT_WBS": "WBS Summary",
        "TT_FinMile": "Financial Milestone"
    }
    return types.get(task_type, "Unknown type")

def interpret_relationship_type(pred_type):
    """Convert relationship type code to readable description"""
    types = {
        "PR_FS": "Finish to Start",
        "PR_SS": "Start to Start",
        "PR_FF": "Finish to Finish",
        "PR_SF": "Start to Finish"
    }
    return types.get(pred_type, "Unknown relationship")

def format_duration(duration_hours):
    """Convert duration from hours to a readable format"""
    try:
        hours = float(duration_hours)
        days = hours / 8  # Assuming 8-hour workdays
        if days >= 1:
            return f"{days:.1f} days"
        return f"{hours:.1f} hours"
    except (ValueError, TypeError):
        return "unknown duration"

def format_lag(lag_hours):
    """Convert lag from hours to a readable format"""
    try:
        hours = float(lag_hours)
        if hours == 0:
            return "no lag"
        if hours < 0:
            return f"{format_duration(abs(hours))} negative lag"
        return f"{format_duration(hours)} lag"
    except (ValueError, TypeError):
        return "unknown lag"


def analyze_relationships(task_id, taskpred_data, task_lookup):
    """Analyze predecessors and successors for an activity"""
    predecessors = []
    successors = []

    for rel in taskpred_data:
        if rel.get('task_id') == task_id:  # This is a predecessor relationship
            pred_id = rel.get('pred_task_id')
            pred_task = task_lookup.get(pred_id, {})
            predecessors.append({
                'name': pred_task.get('task_name', 'Unknown Task'),
                'code': pred_task.get('task_code', ''),
                'type': rel.get('pred_type', ''),
                'lag': rel.get('lag_hr_cnt', 0)
            })
        if rel.get('pred_task_id') == task_id:  # This is a successor relationship
            succ_id = rel.get('task_id')
            succ_task = task_lookup.get(succ_id, {})
            successors.append({
                'name': succ_task.get('task_name', 'Unknown Task'),
                'code': succ_task.get('task_code', ''),
                'type': rel.get('pred_type', ''),
                'lag': rel.get('lag_hr_cnt', 0)
            })

    return predecessors, successors


def analyze_network_logic(task_data, taskpred_data):
    """Analyze the schedule network for logic issues and metrics"""
    metrics = {
        'total_activities': len(task_data),
        'activities_no_pred': 0,
        'activities_no_succ': 0,
        'activities_single_pred': 0,
        'activities_single_succ': 0,
        'activities_multiple_pred': 0,
        'activities_multiple_succ': 0,
        'relationships_with_lag': 0,
        'relationships_with_negative_lag': 0,
        'ss_relationships': 0,
        'ff_relationships': 0,
        'sf_relationships': 0,
        'high_float_activities': 0,
        'negative_float_activities': 0
    }

    # Create lookup dictionaries for relationship counts
    pred_count = {}
    succ_count = {}

    for rel in taskpred_data:
        task_id = rel.get('task_id')
        pred_id = rel.get('pred_task_id')
        lag = float(rel.get('lag_hr_cnt', 0))
        rel_type = rel.get('pred_type', '')

        # Count relationship types
        if rel_type == 'PR_SS':
            metrics['ss_relationships'] += 1
        elif rel_type == 'PR_FF':
            metrics['ff_relationships'] += 1
        elif rel_type == 'PR_SF':
            metrics['sf_relationships'] += 1

        # Track lag metrics
        if lag != 0:
            metrics['relationships_with_lag'] += 1
        if lag < 0:
            metrics['relationships_with_negative_lag'] += 1

        # Count relationships per activity
        pred_count[task_id] = pred_count.get(task_id, 0) + 1
        succ_count[pred_id] = succ_count.get(pred_id, 0) + 1

    # Analyze float and relationship counts
    for task in task_data:
        task_id = task.get('task_id')
        total_float = float(task.get('total_float_hr_cnt', 0))

        # Count activities by predecessor/successor counts
        if task_id not in pred_count:
            metrics['activities_no_pred'] += 1
        elif pred_count[task_id] == 1:
            metrics['activities_single_pred'] += 1
        else:
            metrics['activities_multiple_pred'] += 1

        if task_id not in succ_count:
            metrics['activities_no_succ'] += 1
        elif succ_count[task_id] == 1:
            metrics['activities_single_succ'] += 1
        else:
            metrics['activities_multiple_succ'] += 1

        # Analyze float
        if total_float > 80:  # More than 10 days float (assuming 8-hour days)
            metrics['high_float_activities'] += 1
        if total_float < 0:
            metrics['negative_float_activities'] += 1

    return metrics


def generate_relationship_warnings(predecessors, successors, total_float):
    """Generate warnings about potential relationship issues"""
    warnings = []

    # Check for negative lag
    for pred in predecessors:
        if float(pred.get('lag', 0)) < 0:
            warnings.append(f"Warning: Negative lag on relationship with {pred['name']}")

    # Check for SF relationships
    sf_rels = [p for p in predecessors if p['type'] == 'PR_SF']
    if sf_rels:
        warnings.append(f"Note: Activity has {len(sf_rels)} Start-to-Finish relationship(s), which are unusual")

    # Check float vs relationships
    if not predecessors and float(total_float) > 0:
        warnings.append("Warning: Activity has no predecessors but has positive float")
    if not successors and float(total_float) > 0:
        warnings.append("Warning: Activity has no successors but has positive float")

    return warnings

def generate_wbs_narrative(wbs_data):
    """Generate a natural language description of WBS structure"""
    narratives = []
    for wbs in wbs_data:
        narrative = f"""
Work Breakdown Structure element '{wbs.get('wbs_name', '')}' (Code: {wbs.get('wbs_short_name', '')})
is anticipated to start on {wbs.get('anticip_start_date', 'unknown')} and finish by {wbs.get('anticip_end_date', 'unknown')}.
This WBS element has an original budget of {wbs.get('orig_cost', '0')} and is managed by {wbs.get('obs_id', 'unknown manager')}.
"""
        narratives.append(narrative)
    return "\n".join(narratives)

def generate_project_narrative(project_data):
    """Generate a natural language description of project details"""
    narrative = f"""
This project '{project_data.get('proj_short_name', '')}' was last scheduled on {project_data.get('last_schedule_date', 'unknown date')}.
The project is planned to start on {project_data.get('plan_start_date', 'unknown')} and must finish by {project_data.get('plan_end_date', 'unknown')}.
Current project status: The project is scheduled to finish on {project_data.get('scd_end_date', 'unknown')}.
Project priority level is {project_data.get('priority_num', 'not specified')}.
"""
    return narrative


def generate_semantic_schedule_context(tables):
    """Generate context specifically formatted for LLM understanding"""
    project = tables['PROJECT'].entries()[0]
    wbs = tables['PROJWBS'].entries()
    tasks = tables['TASK'].entries()

    milestones = [task for task in tasks if task.get('task_type') == 'TT_Mile']
    critical_tasks = [task for task in tasks if float(task.get('total_float_hr_cnt', 999999)) <= 0]

    context = {
        "project_identity": {
            "name": project.get('proj_short_name', ''),
            "purpose": f"Schedule exported from P6 version {project.get('export_version', '')}",
            "key_dates": {
                "planned_start": project.get('plan_start_date'),
                "planned_finish": project.get('plan_end_date'),
                "current_finish": project.get('scd_end_date'),
                "data_date": project.get('last_recalc_date')
            }
        },
        "schedule_structure": {
            "phases": [{"name": w.get('wbs_name'), "code": w.get('wbs_short_name')} for w in wbs],
            "major_milestones": [{"name": m.get('task_name'), "date": m.get('target_end_date')} for m in milestones],
            "key_deliverables": [w.get('wbs_name') for w in wbs if w.get('proj_node_flag') != 'Y']
        },
        "timing_analysis": {
            "critical_path_description": f"The schedule contains {len(critical_tasks)} critical activities",
            "float_patterns": f"Average float across activities: {sum(float(t.get('total_float_hr_cnt', 0)) for t in tasks) / len(tasks):.1f} hours",
            "schedule_risks": [
                {"activity": t.get('task_name'), "risk": "Critical"}
                for t in tasks if float(t.get('total_float_hr_cnt', 999999)) <= 0
            ]
        }
    }

    if 'TASKRSRC' in tables:
        resources = tables['TASKRSRC'].entries()
        context["execution_strategy"] = {
            "phase_sequence": "Sequential execution through defined phases",
            "resource_strategy": f"Schedule contains {len(resources)} resource assignments",
            "key_dependencies": [
                {"from": t.get('task_name'), "type": "Critical"}
                for t in critical_tasks
            ]
        }

    return context


def generate_qa_context(tables):
    """Generate common Q&A pairs about the schedule"""
    project = tables['PROJECT'].entries()[0]
    tasks = tables['TASK'].entries()

    # Calculate key metrics
    total_tasks = len(tasks)
    critical_tasks = [t for t in tasks if float(t.get('total_float_hr_cnt', 999999)) <= 0]
    completed_tasks = [t for t in tasks if t.get('status_code') == 'TK_Complete']

    qa_pairs = {
        "timing_questions": {
            "when_will_project_finish": f"Based on current schedule, the project will finish on {project.get('scd_end_date')}",
            "what_is_driving_completion": f"There are {len(critical_tasks)} critical activities driving project completion",
            "which_activities_are_critical": [t.get('task_name') for t in critical_tasks]
        },
        "progress_questions": {
            "what_is_overall_progress": f"{(len(completed_tasks) / total_tasks * 100):.1f}% of activities are complete",
            "how_many_activities_remain": f"{total_tasks - len(completed_tasks)} activities remain to be completed"
        }
    }

    if 'TASKRSRC' in tables:
        resources = tables['TASKRSRC'].entries()
        qa_pairs["resource_questions"] = {
            "what_are_key_resources": [r.get('rsrc_name') for r in resources[:5]],
            "how_many_resources": f"Schedule contains {len(set(r.get('rsrc_id') for r in resources))} unique resources"
        }

    return qa_pairs


def generate_activity_narrative(task_data, tables):
    """Generate a detailed natural language description of activities with relationships"""
    narratives = []

    # Create task lookup dictionary for relationship analysis
    task_lookup = {task.get('task_id'): task for task in task_data}

    # Get relationship data if available
    taskpred_data = tables.get('TASKPRED', {}).entries() if 'TASKPRED' in tables else []

    # Analyze overall network logic
    network_metrics = analyze_network_logic(task_data, taskpred_data)

    # Add network analysis summary at the start
    network_summary = f"""
Schedule Network Analysis:
Total Activities: {network_metrics['total_activities']}
Network Logic:
- Activities without predecessors: {network_metrics['activities_no_pred']}
- Activities without successors: {network_metrics['activities_no_succ']}
- Activities with single predecessor: {network_metrics['activities_single_pred']}
- Activities with multiple predecessors: {network_metrics['activities_multiple_pred']}
- Activities with single successor: {network_metrics['activities_single_succ']}
- Activities with multiple successors: {network_metrics['activities_multiple_succ']}

Relationship Types:
- Start-to-Start relationships: {network_metrics['ss_relationships']}
- Finish-to-Finish relationships: {network_metrics['ff_relationships']}
- Start-to-Finish relationships: {network_metrics['sf_relationships']}
- Relationships with lag: {network_metrics['relationships_with_lag']}
- Relationships with negative lag: {network_metrics['relationships_with_negative_lag']}

Schedule Health Indicators:
- Activities with high float (>10 days): {network_metrics['high_float_activities']}
- Activities with negative float: {network_metrics['negative_float_activities']}
"""

    # Process each activity
    for task in task_data:
        task_id = task.get('task_id')
        status = "completed" if task.get('status_code', '') == "TK_Complete" else \
            "in progress" if task.get('status_code', '') == "TK_Active" else \
                "not started"

        # Calculate various durations
        target_duration = format_duration(task.get('target_drtn_hr_cnt', 0))
        remain_duration = format_duration(task.get('remain_drtn_hr_cnt', 0))

        # Get float values
        total_float = format_duration(task.get('total_float_hr_cnt', 0))
        free_float = format_duration(task.get('free_float_hr_cnt', 0))

        narrative = f"""
Activity '{task.get('task_name', '')}' (ID: {task.get('task_code', '')})
Type: {interpret_task_type(task.get('task_type', ''))}
Status: {status}

Timeline Information:
- Planned Start: {task.get('target_start_date', 'unknown')}
- Planned Finish: {task.get('target_end_date', 'unknown')}
- Actual Start: {task.get('act_start_date', 'Not started')}
- Actual Finish: {task.get('act_end_date', 'Not completed')}
- Original Duration: {target_duration}
- Remaining Duration: {remain_duration}

Schedule Analysis:
- Total Float: {total_float}
- Free Float: {free_float}
- Calendar: {task.get('clndr_id', 'Default calendar')}
- Constraint: {interpret_constraint(task.get('cstr_type', ''))}"""

        # Add constraint date if exists
        if task.get('cstr_date'):
            narrative += f"\n- Constraint Date: {task.get('cstr_date')}"

        # Generate relationship information
        predecessors, successors = analyze_relationships(task_id, taskpred_data, task_lookup)

        if predecessors:
            narrative += "\n\nPredecessor Relationships:"
            for pred in predecessors:
                narrative += f"\n- {pred['name']} (ID: {pred['code']}) - {interpret_relationship_type(pred['type'])} with {format_lag(pred['lag'])}"

        if successors:
            narrative += "\n\nSuccessor Relationships:"
            for succ in successors:
                narrative += f"\n- {succ['name']} (ID: {succ['code']}) - {interpret_relationship_type(succ['type'])} with {format_lag(succ['lag'])}"

        # Add relationship analysis
        if not predecessors:
            narrative += "\n\nThis is a Start activity with no predecessors."
        if not successors:
            narrative += "\n\nThis is a Finish activity with no successors."

        # Add relationship statistics
        narrative += f"\nTotal Relationships: {len(predecessors) + len(successors)} ({len(predecessors)} predecessors, {len(successors)} successors)"

        # Add relationship warnings
        warnings = generate_relationship_warnings(predecessors, successors, task.get('total_float_hr_cnt', 0))
        if warnings:
            narrative += "\n\nLogic Analysis:"
            for warning in warnings:
                narrative += f"\n- {warning}"

        narratives.append(narrative)

    return network_summary + "\n\nDetailed Activity Information:\n" + "\n\n---\n".join(narratives)


# Main Streamlit app
st.title("XER Schedule Narrative Generator")
st.write("Upload an XER file to generate a natural language description of the schedule")

uploaded_file = st.file_uploader("Choose an XER file", type="xer")

if uploaded_file is not None:
    try:
        # Create a temporary file to store the uploaded XER
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xer') as tmp_file:
            tmp_file.write(uploaded_file.read())
            tmp_path = tmp_file.name

        # Read the XER file
        reader = XerReader(tmp_path)
        tables = reader.to_dict()

        # Generate narratives and context
        project_narrative = generate_project_narrative(tables['PROJECT'].entries()[0])
        wbs_narrative = generate_wbs_narrative(tables['PROJWBS'].entries())
        activity_narrative = generate_activity_narrative(tables['TASK'].entries(), tables)

        # Generate LLM-friendly context
        semantic_context = generate_semantic_schedule_context(tables)
        qa_context = generate_qa_context(tables)

        # Combine all narratives
        complete_narrative = f"""
Schedule Context Document
Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
XER File: {uploaded_file.name}
Export Version: {reader.export_version}
Export Date: {reader.export_date}

SEMANTIC CONTEXT FOR LLM:
{json.dumps(semantic_context, indent=2, cls=DateTimeEncoder)}

COMMON QUESTIONS AND ANSWERS:
{json.dumps(qa_context, indent=2, cls=DateTimeEncoder)}

PROJECT OVERVIEW:
{project_narrative}

WORK BREAKDOWN STRUCTURE:
{wbs_narrative}

ACTIVITIES:
{activity_narrative}
"""

        # Display the narrative
        st.subheader("Generated Schedule Narrative")
        st.text_area("Schedule Description", complete_narrative, height=400)

        # Provide download option
        st.download_button(
            label="Download Narrative as Text File",
            data=complete_narrative,
            file_name=f"{reader.file_name}_narrative.txt",
            mime="text/plain"
        )

        # Option to include additional context
        st.subheader("Additional Context")
        user_context = st.text_area("Add any additional context about this schedule:", height=150)

        if user_context:
            complete_narrative += f"\nADDITIONAL CONTEXT:\n{user_context}"

            # Statistics about the schedule
        st.subheader("Schedule Statistics")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Activities", len(tables['TASK'].entries()))
        with col2:
            st.metric("Total WBS Elements", len(tables['PROJWBS'].entries()))
        with col3:
            completed_activities = len([task for task in tables['TASK'].entries()
                                        if task.get('status_code') == "TK_Complete"])
            st.metric("Completed Activities", completed_activities)

    except Exception as e:
        st.error(f"Error processing XER file: {str(e)}")

    finally:
        # Clean up temporary file
        Path(tmp_path).unlink(missing_ok=True)

    st.sidebar.markdown("""
        ## About This App
        This application converts Primavera P6 XER schedule files into natural language descriptions 
        that can be used for context in RAG (Retrieval Augmented Generation) operations.

        ### Features:
        - Generates narrative descriptions of project details
        - Describes WBS structure and hierarchy
        - Provides activity-level descriptions
        - Creates downloadable context files
        - Allows adding custom context

        ### Usage:
        1. Upload an XER file
        2. Review the generated narrative
        3. Add any additional context
        4. Download the complete narrative
        """)