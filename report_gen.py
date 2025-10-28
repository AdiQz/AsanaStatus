import os
import requests
import json
import datetime
from dateutil.relativedelta import relativedelta

BASE_URL = "https://app.asana.com/api/1.0"


class AsanaAPI:
    def __init__(self):
        """Initialize Asana API with environment variables"""
        self.token = os.getenv("ASANA_ACCESS_TOKEN")
        self.section_id = os.getenv("ASANA_SECTION_ID")
        self.team_name = os.getenv("ASANA_TEAM_NAME", "Engagement")  # Default: "Engagement"
        self.priority_field = os.getenv("ASANA_PRIORITY_FIELD", "Priority")  # Default: "Priority"

        if not self.token:
            raise ValueError("ASANA_ACCESS_TOKEN is required in environment variables")
        if not self.section_id:
            raise ValueError("ASANA_SECTION_ID is required in environment variables")

        self.headers = {"Authorization": f"Bearer {self.token}"}

    def fetch_tasks_with_pagination(self, url):
        """Fetch tasks from Asana with pagination"""
        all_tasks = []

        while url:
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                raise Exception(f"API request failed: {response.status_code}")

            data = response.json()
            tasks = data.get("data", [])

            # Filter tasks where Team == ASANA_TEAM_NAME and tag != "Not a bug"
            for task in tasks:
                team_matches = False
                tag_is_not_bug = True
                
                for field in task.get("custom_fields", []):
                    if field.get("name") == "Team" and field.get("display_value") == self.team_name:
                        team_matches = True
                    # elif field.get("name") == "tag" and field.get("display_value") != "Not a bug":
                    #     tag_is_not_bug = True
                
                # Only include task if both conditions are met
                if team_matches and tag_is_not_bug:
                    all_tasks.append(task)

            # Handle pagination
            next_page = data.get("next_page")
            if next_page:
                url = f"{BASE_URL}/sections/{self.section_id}/tasks?opt_fields=custom_fields,created_at,completed,assignee.name&offset={next_page['offset']}&limit=50"
            else:
                url = ""

        return all_tasks

    def get_pending_tasks(self):
        """Fetch the number of incomplete tasks in a section"""
        url = f"{BASE_URL}/sections/{self.section_id}/tasks?limit=50&opt_fields=completed,custom_fields,assignee.name,created_at"
        tasks = self.fetch_tasks_with_pagination(url)
        return sum(1 for task in tasks if not task.get("completed", False))

    def get_incoming_tasks_grouped_by_priority(self):
        """Fetch tasks created this month and group them by Priority"""
        url = f"{BASE_URL}/sections/{self.section_id}/tasks?limit=50&opt_fields=custom_fields,created_at"
        tasks = self.fetch_tasks_with_pagination(url)

        current_month = datetime.datetime.utcnow().strftime("%Y-%m")
        priority_counts = {}

        for task in tasks:
            created_at = task.get("created_at")
            if not created_at:
                continue

            task_date = datetime.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
            if task_date.strftime("%Y-%m") != current_month:
                continue

            for field in task.get("custom_fields", []):
                if field.get("name") == self.priority_field:
                    priority_counts[field.get("display_value", "Unknown")] = (
                            priority_counts.get(field.get("display_value", "Unknown"), 0) + 1
                    )

        return sorted(priority_counts.items(), key=lambda x: x[1], reverse=True)

    def get_tasks_grouped_by_priority(self):
        """Fetch tasks and group them by Priority (ONLY INCOMPLETE TASKS)"""
        url = f"{BASE_URL}/sections/{self.section_id}/tasks?limit=50&opt_fields=custom_fields,completed"
        tasks = self.fetch_tasks_with_pagination(url)

        priority_counts = {}

        for task in tasks:
            if task.get("completed", False):
                continue

            for field in task.get("custom_fields", []):
                if field.get("name") == self.priority_field:
                    priority_counts[field.get("display_value", "Unknown")] = (
                            priority_counts.get(field.get("display_value", "Unknown"), 0) + 1
                    )

        return priority_counts

    def get_tasks_grouped_by_assignee(self):
        """Fetch tasks and group them by Assignee (ONLY INCOMPLETE TASKS)"""
        url = f"{BASE_URL}/sections/{self.section_id}/tasks?limit=50&opt_fields=custom_fields,created_at,assignee.name,completed"
        tasks = self.fetch_tasks_with_pagination(url)

        assignee_counts = {}

        for task in tasks:
            if task.get("completed", False):
                continue

            assignee = task.get("assignee") or {}
            assignee_name = assignee.get("name", "Unassigned")  # Default: "Unassigned"

            assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1

        return sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)


def send_slack_message(slack_message):
    payload = {
        "channel": os.environ['channel_id'],
        "text": slack_message
    }
    slack_url = os.environ['slack_url']
    slack_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['slack_token']}"}

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
    """Main function to execute the Asana API queries"""
    asana_api = AsanaAPI()

    slack_message = ""

    try:
        pending_tasks = asana_api.get_pending_tasks()
        slack_message += f"📌 *Pending Tasks:* {pending_tasks}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching pending tasks: {e}\n"

    try:
        incoming_priority_tasks = asana_api.get_incoming_tasks_grouped_by_priority()
        slack_message += "\n📥 *Incoming Tasks ("+ datetime.datetime.utcnow().strftime("%Y-%m") + ") grouped by Priority:*\n"
        if not incoming_priority_tasks:
            slack_message += "   - 0\n"
        else:
            for priority, count in incoming_priority_tasks:
                slack_message += f"   - {priority}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching incoming tasks by priority: {e}\n"

    try:
        priority_tasks = asana_api.get_tasks_grouped_by_priority()
        slack_message += "\n🔥 *Tasks grouped by Priority:*\n"
        for priority, count in priority_tasks.items():
            slack_message += f"   - {priority}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching tasks by priority: {e}\n"

    try:
        assignee_tasks = asana_api.get_tasks_grouped_by_assignee()
        slack_message += "\n👥 *Tasks grouped by Assignee:*\n"
        for assignee, count in assignee_tasks:
            slack_message += f"   - {assignee}: {count}\n"
    except Exception as e:
        slack_message += f"⚠️ Error fetching tasks by assignee: {e}\n"

    # Send Slack message
    send_slack_message("*Asana Status* \n\n" + slack_message)


if __name__ == "__main__":
    main()
