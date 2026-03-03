import os
import requests
import datetime

PLANE_BASE_URL = "https://plane-tracker.internal.quizizz.com"
PLANE_WORKSPACE_SLUG = "bugs"
PLANE_PROJECT_ID = "7dbe5492-45fa-4989-8c3c-dae60cbd7c28"


class PlaneAPI:
    def __init__(self):
        self.api_key = os.getenv("PLANE_API_KEY", "plane_api_6a6627c951804629a00a4b66fc322a60")
        self.workspace_slug = PLANE_WORKSPACE_SLUG
        self.project_id = PLANE_PROJECT_ID
        self.team_label = os.getenv("PLANE_TEAM_LABEL", "team:Engagement")

        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

        self._engagement_label_id = None
        self._completed_state_ids = None
        self._member_map = None

    def _issues_url(self):
        return f"{PLANE_BASE_URL}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/"

    def _get_engagement_label_id(self):
        if self._engagement_label_id:
            return self._engagement_label_id

        url = f"{PLANE_BASE_URL}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/labels/"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch labels: {response.status_code} {response.text}")

        data = response.json()
        labels = data if isinstance(data, list) else data.get("results", [])
        for label in labels:
            if label.get("name") == self.team_label:
                self._engagement_label_id = label["id"]
                return self._engagement_label_id

        raise ValueError(f"Label '{self.team_label}' not found in project")

    def _get_completed_state_ids(self):
        if self._completed_state_ids is not None:
            return self._completed_state_ids

        url = f"{PLANE_BASE_URL}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/states/"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch states: {response.status_code} {response.text}")

        data = response.json()
        states = data if isinstance(data, list) else data.get("results", [])
        self._completed_state_ids = {
            s["id"] for s in states if s.get("group") in ("completed", "cancelled")
        }
        return self._completed_state_ids

    def _get_member_map(self):
        if self._member_map is not None:
            return self._member_map

        url = f"{PLANE_BASE_URL}/api/v1/workspaces/{self.workspace_slug}/members/"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch members: {response.status_code} {response.text}")

        data = response.json()
        members = data if isinstance(data, list) else data.get("results", [])
        self._member_map = {}
        for entry in members:
            # Plane wraps member info under a "member" key in workspace members
            member = entry.get("member", entry)
            mid = member.get("id")
            display_name = (
                member.get("display_name")
                or f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
                or "Unknown"
            )
            if mid:
                self._member_map[mid] = display_name

        return self._member_map

    def fetch_all_issues(self):
        """Fetch all issues tagged with the Engagement label (paginated, filtered client-side)."""
        label_id = self._get_engagement_label_id()
        matching_issues = []
        cursor = None

        while True:
            params = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor

            response = requests.get(self._issues_url(), headers=self.headers, params=params)
            if response.status_code != 200:
                raise Exception(f"Failed to fetch issues: {response.status_code} {response.text}")

            data = response.json()
            results = data.get("results", [])

            for issue in results:
                if label_id in issue.get("labels", []):
                    matching_issues.append(issue)

            if not data.get("next_page_results"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break

        return matching_issues

    def get_pending_tasks(self):
        """Count incomplete issues."""
        issues = self.fetch_all_issues()
        completed_ids = self._get_completed_state_ids()
        return sum(1 for issue in issues if issue.get("state") not in completed_ids)

    def get_incoming_tasks_grouped_by_priority(self):
        """Issues created this month grouped by priority."""
        issues = self.fetch_all_issues()
        current_month = datetime.datetime.utcnow().strftime("%Y-%m")

        priority_counts = {}
        for issue in issues:
            created_at = issue.get("created_at", "")
            if not created_at:
                continue
            task_date = datetime.datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S")
            if task_date.strftime("%Y-%m") != current_month:
                continue
            priority = issue.get("priority") or "none"
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

        return sorted(priority_counts.items(), key=lambda x: x[1], reverse=True)

    def get_tasks_grouped_by_priority(self):
        """Incomplete issues grouped by priority."""
        issues = self.fetch_all_issues()
        completed_ids = self._get_completed_state_ids()

        priority_counts = {}
        for issue in issues:
            if issue.get("state") in completed_ids:
                continue
            priority = issue.get("priority") or "none"
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

        return priority_counts

    def get_tasks_grouped_by_assignee(self):
        """Incomplete issues grouped by assignee name."""
        issues = self.fetch_all_issues()
        completed_ids = self._get_completed_state_ids()
        member_map = self._get_member_map()

        assignee_counts = {}
        for issue in issues:
            if issue.get("state") in completed_ids:
                continue
            assignees = issue.get("assignees", [])
            if not assignees:
                assignee_counts["Unassigned"] = assignee_counts.get("Unassigned", 0) + 1
            else:
                for assignee_id in assignees:
                    name = member_map.get(assignee_id, "Unknown")
                    assignee_counts[name] = assignee_counts.get(name, 0) + 1

        return sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)


def send_slack_message(slack_message):
    payload = {
        "channel": os.environ["channel_id"],
        "text": slack_message,
    }
    slack_url = os.environ["slack_url"]
    slack_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['slack_token']}",
    }

    response = requests.post(slack_url, headers=slack_headers, json=payload)

    if response.status_code == 200:
        response_data = response.json()
        if response_data.get("ok"):
            print("Message posted successfully!")
        else:
            print("Failed to post message:", response_data.get("error"))
    else:
        print(f"Request failed with status code {response.status_code}: {response.text}")


def main():
    plane_api = PlaneAPI()
    slack_message = ""

    try:
        pending_tasks = plane_api.get_pending_tasks()
        slack_message += f"📌 *Pending Tasks:* {pending_tasks}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching pending tasks: {e}\n"

    try:
        incoming_priority_tasks = plane_api.get_incoming_tasks_grouped_by_priority()
        slack_message += "\n📥 *Incoming Tasks (" + datetime.datetime.utcnow().strftime("%Y-%m") + ") grouped by Priority:*\n"
        if not incoming_priority_tasks:
            slack_message += "   - 0\n"
        else:
            for priority, count in incoming_priority_tasks:
                slack_message += f"   - {priority}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching incoming tasks by priority: {e}\n"

    try:
        priority_tasks = plane_api.get_tasks_grouped_by_priority()
        slack_message += "\n🔥 *Tasks grouped by Priority:*\n"
        for priority, count in priority_tasks.items():
            slack_message += f"   - {priority}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching tasks by priority: {e}\n"

    try:
        assignee_tasks = plane_api.get_tasks_grouped_by_assignee()
        slack_message += "\n👥 *Tasks grouped by Assignee:*\n"
        for assignee, count in assignee_tasks:
            slack_message += f"   - {assignee}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching tasks by assignee: {e}\n"

    send_slack_message("*Plane Status* \n\n" + slack_message)


if __name__ == "__main__":
    main()