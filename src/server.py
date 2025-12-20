#!/usr/bin/env python3
import os
import httpx
from fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()
base_url = os.getenv("CANVAS_BASE_URL")
access_token = os.getenv("CANVAS_ACCESS_TOKEN")

mcp = FastMCP("poke-canvas-mcp")

@mcp.tool(description="get a list of all the canvas courses")
def get_courses(_=None):
    url = base_url+"/api/v1/courses?per_page=1000"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = httpx.get(url, headers=headers)
    return r.json();


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    
    print(f"Starting FastMCP server on {host}:{port}")
    
    mcp.run(
        transport="http",
        host=host,
        port=port,
        stateless_http=True
    )
