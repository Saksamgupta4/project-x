"""
GreenPath Railway Setup Script
Automatically deploys to multiple Railway instances.

Usage:
  python3 setup_railway.py

Fill in your credentials in the CONFIG section below.
"""

import json
import urllib.request
import urllib.error
import sys
import time

# ── FILL THESE IN ─────────────────────────────────────────────
CONFIG = {
    "github_repo":   "https://github.com/YOUR_USERNAME/YOUR_REPO",
    "github_branch": "main",
    "supabase_url":  "https://YOUR_PROJECT.supabase.co",
    "supabase_key":  "YOUR_SUPABASE_ANON_KEY",
    "nordvpn_user":  "YOUR_NORDVPN_SOCKS5_USER",
    "nordvpn_pass":  "YOUR_NORDVPN_SOCKS5_PASS",
    "scorm_config":  '{"lms":{"url":"https://inco.docebosaas.com","learning_plan_id":41,"learning_plan_slug":"green-pathways"},"courses":[{"id":290,"name":"Module 1: Foundations of sustainability","lessons":6,"required":true,"slug":"module-1-foundations-of-sustainability"},{"id":291,"name":"Module 2: Energy Transition","lessons":6,"required":true,"slug":"module-2-energy-transition"},{"id":292,"name":"Module 3: Career Pathways","lessons":5,"required":true,"slug":"module-3-career-pathways"},{"id":293,"name":"Module 4: Professional Skills","lessons":5,"required":false,"slug":"module-4-professional-skills"}],"simulation":{"min_minutes_per_lesson":10,"max_minutes_per_lesson":20,"days_between_modules_min":4,"days_between_modules_max":4},"test_mode":{"enabled":false,"speed_multiplier":3600},"proxy":{"type":"socks5","port":1080,"username":"","password":"","servers":{"in":"in.socks.nordhold.net","ae":"ae.socks.nordhold.net","gb":"gb.socks.nordhold.net","us":"us.socks.nordhold.net","de":"de.socks.nordhold.net","fr":"fr.socks.nordhold.net","sg":"sg.socks.nordhold.net","au":"au.socks.nordhold.net","ca":"ca.socks.nordhold.net","nl":"nl.socks.nordhold.net"}},"accounts":[]}',
    "railway_tokens": [
        "YOUR_RAILWAY_TOKEN_1",
        "YOUR_RAILWAY_TOKEN_2",
        "YOUR_RAILWAY_TOKEN_3",
        "YOUR_RAILWAY_TOKEN_4",
        "YOUR_RAILWAY_TOKEN_5",
        "YOUR_RAILWAY_TOKEN_6",
    ]
}
# ─────────────────────────────────────────────────────────────

RAILWAY_API = "https://backboard.railway.app/graphql/v2"

def railway_query(token, query, variables=None):
    data    = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}"
    }
    req = urllib.request.Request(RAILWAY_API, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            if "errors" in result:
                print(f"  GraphQL error: {result['errors'][0]['message']}")
                return None
            return result.get("data")
    except Exception as e:
        print(f"  Request error: {e}")
        return None

def create_project(token, name):
    q = """
    mutation CreateProject($name: String!) {
        projectCreate(input: { name: $name }) {
            id
            name
        }
    }
    """
    data = railway_query(token, q, {"name": name})
    if data:
        return data.get("projectCreate", {})
    return None

def get_project_id(token):
    q = """
    query {
        me {
            projects {
                edges {
                    node {
                        id
                        name
                    }
                }
            }
        }
    }
    """
    data = railway_query(token, q)
    if data:
        projects = data.get("me", {}).get("projects", {}).get("edges", [])
        return [p["node"] for p in projects]
    return []

def create_service_from_github(token, project_id, repo_url, branch, instance_id, env_vars):
    # Create service
    q = """
    mutation ServiceCreate($projectId: String!, $name: String!) {
        serviceCreate(input: { projectId: $projectId, name: $name }) {
            id
            name
        }
    }
    """
    data = railway_query(token, q, {"projectId": project_id, "name": f"greenpath-{instance_id}"})
    if not data:
        return None
    service_id = data.get("serviceCreate", {}).get("id")
    if not service_id:
        return None
    print(f"  Service created: {service_id}")

    # Connect GitHub
    q2 = """
    mutation ServiceConnect($id: String!, $repo: String!, $branch: String!) {
        serviceConnect(id: $id, input: { repo: $repo, branch: $branch }) {
            id
        }
    }
    """
    railway_query(token, q2, {"id": service_id, "repo": repo_url, "branch": branch})

    # Set environment variables
    for key, value in env_vars.items():
        q3 = """
        mutation VariableUpsert($projectId: String!, $serviceId: String!, $name: String!, $value: String!) {
            variableUpsert(input: {
                projectId: $projectId
                serviceId: $serviceId
                name: $name
                value: $value
                environmentId: ""
            })
        }
        """
        railway_query(token, q3, {
            "projectId": project_id,
            "serviceId": service_id,
            "name": key,
            "value": value
        })
        print(f"  Set: {key}")

    return service_id

def main():
    print("\n🌿 GREENPATH RAILWAY SETUP")
    print("=" * 50)
    print(f"Instances to create: {len(CONFIG['railway_tokens'])}")
    print(f"GitHub repo: {CONFIG['github_repo']}")
    print("=" * 50)

    results = []

    for idx, token in enumerate(CONFIG["railway_tokens"]):
        instance_id = f"batch{idx + 1}"
        print(f"\n[{idx+1}/{len(CONFIG['railway_tokens'])}] Setting up {instance_id}...")

        # Create project
        project = create_project(token, f"greenpath-{instance_id}")
        if not project:
            print(f"  ❌ Failed to create project")
            continue

        project_id = project["id"]
        print(f"  Project: {project['name']} ({project_id})")

        # Env vars for this instance
        env_vars = {
            "INSTANCE_ID":   instance_id,
            "SUPABASE_URL":  CONFIG["supabase_url"],
            "SUPABASE_KEY":  CONFIG["supabase_key"],
            "NORDVPN_USER":  CONFIG["nordvpn_user"],
            "NORDVPN_PASS":  CONFIG["nordvpn_pass"],
            "SCORM_CONFIG":  CONFIG["scorm_config"],
            "PORT":          "8080",
        }

        # Create service
        service_id = create_service_from_github(
            token, project_id,
            CONFIG["github_repo"],
            CONFIG["github_branch"],
            instance_id, env_vars
        )

        if service_id:
            print(f"  ✅ {instance_id} deployed!")
            results.append({
                "instance": instance_id,
                "project":  project["name"],
                "status":   "deployed"
            })
        else:
            print(f"  ❌ Failed to deploy {instance_id}")

        time.sleep(2)  # Rate limit

    print("\n" + "=" * 50)
    print("📊 RESULTS:")
    for r in results:
        print(f"  ✅ {r['instance']} → {r['project']}")
    print(f"\n✅ {len(results)}/{len(CONFIG['railway_tokens'])} instances deployed!")
    print("\nNext steps:")
    print("1. Wait 5 mins for all instances to build and start")
    print("2. Open each Railway dashboard")
    print("3. Add accounts via bulk import on any instance")
    print("4. Click Start All on each instance")
    print("=" * 50)

if __name__ == "__main__":
    main()
