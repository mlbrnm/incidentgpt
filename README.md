Your README.md file should be minimally multiple paragraphs in length, and should explain what your project is, what each of the files you wrote for the project contains and does, and if you debated certain design choices, explaining why you made them. Ensure you allocate sufficient time and energy to writing a README.md that documents your project thoroughly. Be proud of it! A README.md in the neighborhood of 750 words is likely to be sufficient for describing your project and all aspects of its functionality. If unable to reach that threshold, that probably means your project is insufficiently complex.

This project is designed to help diagnose problems in an IT support environment. It uses semantic search to find past issues similar to the new issue, then uses a language model to provide a summary of the previous solutions that may be applicable to the new issue. Problems often show up repeatedly, and past solutions can provide valuable incites into how to deal with new issues. In an IT support environment, staff are often on call outside of regular hours and a quick set of AI-provided suggestions may help point them towards a resolution quickly even if they're not in the mindset for problem solving.

ServiceNow is a ticketing system used by many organizations. People submit issues or requests to various departments, and ServiceNow tracks the actions taken and final resolution of the issues.

Zabbix is a server monitoring system. It installs an agent on monitored devices which runs various reports to identify and categorize potential issues worth addressing, and displays these in various dashboards.

The helper scripts in the 'tools' folder are used to turn the old incidents into a format suitable for RAG (retrieval augmented generation) and semantic search. When the old incidents are exported from ServiceNow (or pulled in from the API, in the case of Zabbix) they are in key:value format and need to be transformed into a problem:solution format with accompanying metadata that may be useful to the user. I chose to use the CSV export for ServiceNow as I didn't initially realize API access was possible. In the future, I would like to switch the ServiceNow implementation to also work over the API to make long term maintenance of the solution database more automatable.

The ServiceNow system for formatting these previous issues is qutie simple. It takes the CSV file and turns each row into a Record object, which just maps the fields from the CSV to appropriately named properties on the Record class. There is a function to parse the date into a datetime object. After it has converted all the rows into Record objects, it simply loops through them all, printing each Record to a .txt file in an appropriate format that looks like this:

```
<Ticket Number> | <Ticket Date>
Submitted by: <submitter> | Resovled by: <resolver>
---- Problem:
<problem text submitted>
---- Solution:
<solution to problem reported>
---------------------------------------------------
```
Once this text file has been saved, it can be uploaded to the Qdrant database via PrivateGPT.

The Zabbix process works similarly, but instead pulls in incidents over the Zabbix API instead of by reading a CSV export.

The main part of the application consists of app.py (the Flask framework), and servicenowapi.py and zabbixapi.py, which are used to automate the retrieval of new (unsolved) incidents from the two services. The program checks for new incidents every 60 seconds and if found, submits them via POST request to the Flask app. The Flask app then integrates with a project called [PrivateGPT](https://github.com/zylon-ai/private-gpt), which serves a vector database via Qdrant and a large language model via Ollama.

PrivateGPT handles a lot of the specifics, but basically the new incident is put into a format identical to the exported resolved incidents (as shown above). This is compared to the previous incidents based on a vector search in Qdrant, and the top 5 most similar previous incidents are retrieved. The new problem, along with the 5 similar previous incidents and their solutions, are submitted to the Llama3.1-8B LLM via Ollama, with a prompt similar to this:

```
<old incidents + solutions>
Based on the above previously resolved incidents and their solutions, provide a potential solution to this new incident:
<new incident>
Be concise. If the previous incidents do not seem relevant, simply state as such and do not make things up.
```

The Zabbix page does not use the LLM part. I didn't find it ever provided any helpful comments in that context. Instead, the Zabbix page is more like a live monitoring dashboard. It shows current issues the Zabbix server is complaining about, and what people did to resolve similar issues previously. In Zabbix, the problems and solutions are usually less than a sentence each, so it's easy to just take a glance at the past solutions instead of having the LLM summarize it all.

Apart from the vector database which is handled outside the program, there are a few Sqlite3 databases in place within the program. These retain the history of new solutions between server restarts, and ensure that the looping API retrievals only pull in new incidents rather than processing the same set every minute.

The front end interface of this program is all Flask / Jinja as learned in CS50. There are two pages, one for ServiceNow and one for Zabbix. They both work similarly, but provide different views. The ServiceNow view is chronological and provides the LLM generated soluit

This project has some significant rough points and if I were designing it again, there is a lot I would do differently:
1. Unncesarry HTTP requests - I think that following the API retrievals every minute, it would make more sense to keep everything within the Python instance rather than running the scripts separately and having them interact via POST requests.
2. Inconsistency - the ServiceNow and Zabbix parts work kind of differently, and the Zabbix functionality was shoehorned in afterwards so some functions are not really being used how they were originally designed.
3. Using .py file to store credentials - I have since learned of .env and similar, which make more sense than using a .py file to store the API keys and such.
