"""The NLU step: retries, failure handling and accounting."""

from pedidos_bot.engine.actions import AddItem, Unknown
from pedidos_bot.engine.nlu import Nlu, system_prompt
from pedidos_bot.llm.base import LLMError, LLMRequest, LLMResult
from pedidos_bot.llm.fake import ScriptedLLM
from tests.factories import make_config

CONFIG = make_config()


async def test_valid_answer_reports_usage_and_latency() -> None:
    llm = ScriptedLLM([[AddItem(product="clasica")]])
    result = await Nlu(llm, CONFIG).interpret("una clasica")
    assert result.actions == (AddItem(product="clasica"),)
    assert result.attempts == 1 and not result.failed
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    assert llm.requests[0].json_schema is not None


async def test_invalid_answer_is_retried_with_the_error() -> None:
    llm = ScriptedLLM(["no soy json", [AddItem(product="clasica")]])
    result = await Nlu(llm, CONFIG).interpret("una clasica")
    assert result.actions == (AddItem(product="clasica"),)
    assert result.attempts == 2 and not result.failed
    assert "inválido" in llm.requests[1].messages[-1].content


async def test_two_bad_answers_become_unknown() -> None:
    result = await Nlu(ScriptedLLM(["x", "y"]), CONFIG).interpret("algo")
    assert result.actions == (Unknown(),)
    assert result.failed


async def test_empty_answer_becomes_unknown() -> None:
    result = await Nlu(ScriptedLLM([[]]), CONFIG).interpret("algo")
    assert result.actions == (Unknown(),)


async def test_provider_failure_becomes_unknown() -> None:
    class Down:
        async def complete(self, request: LLMRequest) -> LLMResult:
            raise LLMError("connection refused")

    result = await Nlu(Down(), CONFIG).interpret("algo")
    assert result.actions == (Unknown(),)
    assert result.failed


def test_prompt_has_product_names_but_no_prices() -> None:
    prompt = system_prompt(CONFIG)
    product = next(iter(CONFIG.menu.products()))
    assert product.name in prompt
    assert str(product.price) not in prompt
