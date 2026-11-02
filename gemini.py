import os
from typing import Annotated

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict


# ---------------------------
# 1) Simple tool
# ---------------------------
@tool
def get_weather(city: str) -> str:
    """Return the weather for a city."""
    weather_map = {
        "paris": "24°C and sunny",
        "london": "18°C and cloudy",
        "tokyo": "27°C and humid",
    }
    return weather_map.get(city.lower(), f"I don't have weather data for {city}.")


# ---------------------------
# 2) State
# ---------------------------
class State(TypedDict):
    messages: Annotated[list, add_messages]


# ---------------------------
# 3) LLM with tool calling
# ---------------------------
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.environ["GEMINI_API_KEY"],
).bind_tools([get_weather])


def call_agent(state: State):
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


# ---------------------------
# 4) Build graph
# ---------------------------
graph = StateGraph(State)

graph.add_node("agent", call_agent)
graph.add_node("tools", ToolNode([get_weather]))

graph.add_edge(START, "agent")
graph.add_conditional_edges(
    "agent",
    tools_condition,
    {
        "tools": "tools",
        "end": END,
    },
)
graph.add_edge("tools", "agent")

app = graph.compile()


# ---------------------------
# 5) Run it
# ---------------------------
result = app.invoke({
    "messages": [HumanMessage(content="What's the weather in Paris?")]
})

print(result["messages"][-1].content)