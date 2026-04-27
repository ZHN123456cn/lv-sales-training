from dotenv import load_dotenv
import os
import json
import re

from langchain_community.utilities import SearchApiAPIWrapper
from langchain.tools import Tool
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate

load_dotenv()  
deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')

# 实例化SerpAPIWrapper
search = SearchApiAPIWrapper()

def calculate_power(inputs:str) -> str:
    """计算一个数的乘方   
    """
    try:
        print("inputs:", inputs )

        json_data = json.loads(inputs)
        base = json_data["base"]
        exponent = json_data["exponent"]
        result = base ** exponent
        return f"{base}的{exponent}次方等于{result}"

    except (ValueError, KeyError) as e:
        return f"计算出错：请确保输入了有效的底数和指数。错误信息：{str(e)}"

# 准备工具列表
tools = [
    Tool(
        name="Search",
        func=search.run,
        description="当大模型没有相关知识时，用于搜索知识",
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词"
                }
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="Power",
        func=calculate_power,  
        description="计算一个数的乘方",
        args_schema={
            "type": "object",
            "properties": {
                "base": {
                    "type": "number",
                    "description": "乘方的底数"
                },
                "exponent": {
                    "type": "number",
                    "description": "乘方的指数"
                }
            },
            "required": ["base", "exponent"]
        }
    )
]

# 构建工具schema
tool_schemas = []
for tool in tools:
    schema = {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_schema if hasattr(tool, 'args_schema') else {}
        }
    }
    tool_schemas.append(schema)

# 构建工具描述字符串（包含schema）
tool_descriptions = []
for tool in tools:
    desc = f"{tool.name}: {tool.description}"
    if hasattr(tool, 'args_schema'):
        desc += f"，参数格式：{json.dumps(tool.args_schema, ensure_ascii=False)}"
    tool_descriptions.append(desc)
tool_descriptions = "\n".join(tool_descriptions)
tool_names = ", ".join([tool.name for tool in tools])

print("tool desc:" , tool_descriptions)

# 定义模板
template = """尽你所能回答以下问题。你可以使用以下工具:

{tools}

当需要调用工具时，请使用以下格式输出JSON：
```json
{{
    "name": "工具名称",
    "parameters": {{
        "参数名": 参数值
    }}
}}
```

如果不需要调用工具，可以直接回答问题。

开始！

Question: {input}
{agent_scratchpad}"""

# 创建提示模板
prompt = PromptTemplate(
    template=template,
    input_variables=["input", "agent_scratchpad", "tools"]
)

# 选择要使用的LLM
llm = ChatOpenAI(temperature=0.7,
            base_url="https://api.deepseek.com/v1",
            api_key=deepseek_api_key,  # 替换成自己的 api_key
            model_name="deepseek-chat",
            streaming=True,  # 启用流式输出
            timeout=60,
            max_retries=3)

# 实现Agent Loop
def agent_loop(input_question):
    """实现Agent Loop功能"""
    # 初始化对话历史
    agent_scratchpad = ""
    
    while True:
        # 1. 将任务、工具的名字和描述发送给大模型
        formatted_prompt = prompt.format(
            input=input_question,
            agent_scratchpad=agent_scratchpad,
            tools=tool_descriptions
        )
        
        # 调用大模型获取响应
        response = llm.invoke(formatted_prompt)
        response_text = response.content
        print("Model response:", response_text)
        
        # 2. 检查是否包含工具调用
        if "```json" in response_text and "```" in response_text:
            # 提取JSON部分
            json_start = response_text.find("```json") + len("```json")
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
            
            try:
                tool_call = json.loads(json_str)
                action_name = tool_call["name"]
                action_input = tool_call["parameters"]
                
                print(f"Tool call: {action_name} with parameters {action_input}")
                
                # 3. 查找对应的工具
                selected_tool = None
                for tool in tools:
                    if tool.name == action_name:
                        selected_tool = tool
                        break
                
                if selected_tool:
                    # 调用工具
                    try:
                        if action_name == "Search":
                            # Search工具直接接受字符串
                            observation = selected_tool.func(action_input["query"])
                        else:
                            # 其他工具需要JSON字符串
                            input_str = json.dumps(action_input)
                            observation = selected_tool.func(input_str)
                        print(f"Observation: {observation}")
                        
                        # 4. 将工具执行的结果带给大模型
                        agent_scratchpad += f"Tool call: {action_name} with parameters {action_input}\n"
                        agent_scratchpad += f"Observation: {observation}\n"
                    except Exception as e:
                        print(f"Error executing tool: {str(e)}")
                        agent_scratchpad += f"Tool call: {action_name} with parameters {action_input}\n"
                        agent_scratchpad += f"Observation: 执行工具时出错: {str(e)}\n"
                else:
                    print(f"Unknown tool: {action_name}")
                    agent_scratchpad += f"Tool call: {action_name} with parameters {action_input}\n"
                    agent_scratchpad += f"Observation: 未知工具: {action_name}\n"
            except json.JSONDecodeError as e:
                print(f"Invalid JSON format: {str(e)}")
                agent_scratchpad += f"Invalid tool call format: {json_str}\n"
                agent_scratchpad += f"Observation: JSON解析错误: {str(e)}\n"
        else:
            # 不需要调用工具，直接回答
            print("Final Answer:", response_text)
            return response_text

# 测试Agent Loop
if __name__ == "__main__":
    # 测试计算乘方
    # agent_loop("5的10次方是多少？")
    
    # 测试搜索功能
    # agent_loop("当前Agent的最新进展是什么？请用中文回答。")
    
    # 测试搜索功能
    agent_loop("什么是openclaw？请用中文回答。")
    # agent_loop("2的10次方是多少？")
