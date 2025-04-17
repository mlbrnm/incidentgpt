# IncidentAssist
#### Description:
This project is designed to help diagnose problems in an IT support environment. It uses semantic search to find past issues similar to the new issue, then uses a language model to provide a summary of the previous solutions that may be applicable to the new issue. Problems often show up repeatedly, and past solutions can provide valuable incites into how to deal with new issues. In an IT support environment, staff are often on call outside of regular hours and a quick set of AI-provided suggestions may help point them towards a resolution quickly even if they're not in the mindset for problem solving.

ServiceNow is a ticketing system used by many organizations. People submit issues or requests to various departments, and ServiceNow tracks the actions taken and final resolution of the issues.

I have two additional tools which automate the process of pulling past incidents from ServiceNow, as well as wiki articles from Wiki.js, into the PrivateGPT RAG database. They can be found in the tools folder.

The ServiceNow tool basically takes all the resolved incidents and puts them in a format like this:

```
<Ticket Number> | <Ticket Date>
Submitted by: <submitter> | Resovled by: <resolver>
---- Problem:
<configuration item>
<problem text submitted>
---- Solution:
<solution to problem reported>
---------------------------------------------------
```

Which we are then able to perform a semantic search on.

This tool monitors ServiceNow for new unresolved incidents, pinging the API every 5 minutes and storing the new incidents in a SQLite database.


For each new incident, the new problem text, along with the 5 similar previous incidents and their solutions, are submitted to the Llama3.1-8B LLM via Ollama, with a prompt similar to this:

```
<old incidents + solutions>
Based on the above previously resolved incidents and their solutions, provide a potential solution to this new incident:
<new incident>
Be concise. If the previous incidents do not seem relevant, simply state as such and do not make things up.
```

These are all viewable from the Flask / SocketIO front end which updates in real time as solutions are added.