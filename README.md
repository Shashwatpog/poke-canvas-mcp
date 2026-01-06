# Canvas MCP for Poke

Custom MCP server that uses Canvas LMS APIs to provide grades, deadlines, assignments, etc context to Poke 

What is [Poke](https://poke.com/)? - AI assistant that I use day to day for managing daily tasks, emails, to do lists, etc. via texting on imessage

## Overview

- **Authentication**: Canvas Personal Access Token
- **Data Access**: Read only, Canvas student account

## Questions this MCP can help answer with Poke

- What are my upcoming deadlines?
- What announcements were posted recently?
- What does my upcoming week look like?
- What does my academic day today look like?
- Did any of my assignments get graded recently?

## Setting Up Canvas Access Token 

1. Log in to your canvas account
2. Go to your Account, Settings Page
3. Scroll to Approved Integrations 
4. Create New Access Token (copy the token as you won't see it again)

## Quick Start

```bash 
git clone https://github.com/Shashwatpog/poke-canvas-mcp
cd poke-canvas-mcp

# Create a virtual environment and install required packages
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your Canvas credentials

# Run
python src/server.py
# In a different terminal run the inspector for testing
npx @modelcontextprotocol/inspector
```
Server runs on http://localhost:8000/mcp, connect using "Streamable HTTP" Transport

## Environment Variables

```bash
CANVAS_BASE_URL=https://your_school.instructure.com 
CANVAS_ACCESS_TOKEN=you_canvas_access_token
```

## Deploying

The [render.yaml](render.yaml) file contains configuration to deploy on render

1. Fork this repository
2. Create a new web service on render
3. Connect your forked repository
4. Enter the environment variables and deploy

## Poke Integration

After deploying the MCP, add the MCP URL in poke settings at [poke.com/settings/connections](https://poke.com/settings/connections)

To test it out, ask poke "What do I need to do today on canvas?" "What are my upcoming deadlines on canvas?"

Or you can ask poke explicitly to use the MCP by mentioning its connection name and asking it to call one of the tools directly. 

Example: "Use Canvas MCP connection's get_today_summary tool"

## Features and MCP Tools

#### **1. get_today_summary**

- This tool is designed for daily check-ins and notifications.
- Used for creating a list of deadlines, announcements, grade notifications in a 24 to 48 hour window with any overdue assignments in the past week. 

#### **2. get_upcoming_assignments**

- This tool aggregates assignments across current courses including upcoming deadlines, overdue items and sorts with most urgent items first. 

#### **3. get_recent_announcements**

- This tool returns all the recent announcements with various options: limiting number of courses, number of announcements per course during look up. 
- Also has option to get full message body in case user wants a summary of the full announcement message.

#### **4. get_recently_graded**

- This tool detects when an assignment is newly graded and uses canvas planner feed. Used to notify user when their grades are released for quizzes and assignments.

#### **5. get_week_ahead**

- This tool returns upcoming assignments, deadlines, announcements, calendar events in the upcoming week to help user plan their week better.

#### **6. list_courses_raw**

- This tool returns list of all the courses that are active.

#### **7. get_dashboard_cards**

- This tool returns list of all the courses currently on the dashboard in the order set by the user. Used for easy filtering of courses.

#### **8. get_course_assignments**

- This tool returns all the upcoming assignments for a specific course with the option to include overdue assignments using course id.


## Problems I faced

Managing a personal email, school email, work email, canvas notifications, and todo lists is pretty hard and time consuming. 

I often found myself trying to keep up with canvas notifications and deadlines and losing track of them in the middle of the semester when classes pick up pace. 

Poke as an assistant was very good at keeping track of my emails but everytime I had to keep track of canvas deadlines I had to manually take a screen shot of the canvas calendar and feed the image to poke or ask it to create events manually.

The solution to this pain point is this Canvas MCP Integration!

## How this MCP works

I was able to go through the Canvas LMS API documentation (which was difficult and confusing) along with manually playing around in the Networks tab to find various API endpoints that we can hit to get student and course data. 

The MCP aggregates and normalizes multiple endpoints: 

| Endpoint | Description 
|------------|-------------
|GET /api/v1/dashboard/dashboard_cards| Fetch active courses in dashboard order
|GET /api/v1/courses/:course_id/assignments?include[]=submission| Fetch assignments with submission metadata
|GET /api/v1/courses/:course_id/discussion_topics?only_announcements=true| Fetch course announcements
|GET /api/v1/planner/items| Planner feed for events, assignments, quizzes, and grades
|GET /api/v1/courses| Fetch all enrolled courses

## Challenges

Some challenges I ran into definitely includes finding the right API endpoints as [Canvas LMS documentation](https://developerdocs.instructure.com/services/canvas) has a bunch of different APIs listed under it. It was a hassle to figure out which ones would be used when especially given the fact that half of the API endpoints listed there can only be accessed with Admin access or developer keys.

Students like myself don't have that authorization and cannot generate developer keys. Students, however, can generate Access Tokens, which we have used for this MCP. 

Hence, I had to manually hit each API endpoint before using it to make sure I had access to them.

As the response in json format also included a lot of metadata and details that were unnecessary, I decided to look through the responses and figure out which items to keep and feed as context to poke. For example, when fetching courses, I decided to only return the course id and name in the mcp tool. 

Another challenge I faced was Canvas keeping my old courses from previous semesters active and enrolled. The only work around to this was using the prefix for each term such as "25-FS" which shows 2025 Fall Semester courses or "26-SS" which shows 2026 Spring Semester courses.

Moreover, I also had to normalize the timestamps in various fields such as "due_at", "graded_at", etc. to UTC to make it consistent and easy to filter deadlines. 

Shoutout to [exa.ai](exa.ai): made it easy for me to search up api endpoints for Canvas after losing my mind in Canvas API docs!

## Future Goals

- No plans so far, will test it out for a few weeks and if I have any issues, will add more tools / update the current ones