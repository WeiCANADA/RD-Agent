import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import fire
import pandas as pd

from rdagent.app.qlib_rd_loop.conf import FACTOR_FROM_REPORT_PROP_SETTING
from rdagent.app.qlib_rd_loop.factor import FactorRDLoop
from rdagent.components.document_reader.document_reader import (
    extract_first_page_screenshot_from_pdf,
    load_and_process_pdfs_by_langchain,
)
from rdagent.core.conf import RD_AGENT_SETTINGS
from rdagent.core.proposal import Hypothesis, HypothesisFeedback
from rdagent.log import rdagent_logger as logger
from rdagent.oai.llm_utils import APIBackend
from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorExperiment
from rdagent.scenarios.qlib.factor_experiment_loader.json_loader import (
    FactorExperimentLoaderFromDict,
)
from rdagent.scenarios.qlib.factor_experiment_loader.pdf_loader import (
    FactorExperimentLoaderFromPDFfiles,
    check_factor_viability,
    extract_factors_from_report_dict,
    merge_file_to_factor_dict_to_factor_dict,
)
from rdagent.utils.agent.tpl import T
from rdagent.utils.workflow import LoopMeta

SUPPORTED_REPORT_SUFFIXES = {".pdf", ".csv", ".json", ".md", ".txt"}


def generate_hypothesis(factor_result: dict, report_content: str) -> str:
    """
    Generate a hypothesis based on factor results and report content.

    Args:
        factor_result (dict): The results of the factor analysis.
        report_content (str): The content of the report.

    Returns:
        str: The generated hypothesis.
    """
    system_prompt = T(".prompts:hypothesis_generation.system").r()
    user_prompt = T(".prompts:hypothesis_generation.user").r(
        factor_descriptions=json.dumps(factor_result), report_content=report_content
    )

    response = APIBackend().build_messages_and_create_chat_completion(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        json_mode=True,
        json_target_type=Dict[str, str],
    )

    response_json = json.loads(response)

    return Hypothesis(
        hypothesis=response_json.get("hypothesis", "No hypothesis provided"),
        reason=response_json.get("reason", "No reason provided"),
        concise_reason=response_json.get("concise_reason", "No concise reason provided"),
        concise_observation=response_json.get("concise_observation", "No concise observation provided"),
        concise_justification=response_json.get("concise_justification", "No concise justification provided"),
        concise_knowledge=response_json.get("concise_knowledge", "No concise knowledge provided"),
    )


def extract_hypothesis_and_exp_from_reports(report_file_path: str) -> QlibFactorExperiment | None:
    """
    Extract hypothesis and experiment details from report files.

    Args:
        report_file_path (str): Path to the report file.

    Returns:
        QlibFactorExperiment: An instance of QlibFactorExperiment containing the extracted details.
        None: If no valid experiment is found in the report.
    """
    report_path = Path(report_file_path)

    if report_path.suffix.lower() == ".pdf":
        exp = FactorExperimentLoaderFromPDFfiles().load(report_file_path)
        if exp is None or exp.sub_tasks == []:
            return None

        pdf_screenshot = extract_first_page_screenshot_from_pdf(report_file_path)
        logger.log_object(pdf_screenshot, tag="load_pdf_screenshot")

        docs_dict = load_and_process_pdfs_by_langchain(report_file_path)
    else:
        docs_dict = {str(report_path): _load_non_pdf_report_content(report_path)}
        useful_report_dict = {str(report_path): {"class": 1}}
        file_to_factor_result = extract_factors_from_report_dict(docs_dict, useful_report_dict)
        factor_dict = merge_file_to_factor_dict_to_factor_dict(file_to_factor_result)
        _, filtered_factor_dict = check_factor_viability(factor_dict)
        exp = FactorExperimentLoaderFromDict().load(filtered_factor_dict)
        if exp is None or exp.sub_tasks == []:
            return None

    factor_result = {
        task.factor_name: {
            "description": task.factor_description,
            "formulation": task.factor_formulation,
            "variables": task.variables,
            "resources": task.factor_resources,
        }
        for task in exp.sub_tasks
    }

    report_content = "\n".join(docs_dict.values())
    hypothesis = generate_hypothesis(factor_result, report_content)
    exp.hypothesis = hypothesis
    return exp


def _load_non_pdf_report_content(report_path: Path) -> str:
    suffix = report_path.suffix.lower()

    if suffix == ".csv":
        # Use sample rows to cap prompt size while preserving schema context.
        try:
            sample_df = pd.read_csv(report_path, nrows=200)
            return sample_df.to_csv(index=False)
        except Exception:
            return report_path.read_text(encoding="utf-8", errors="ignore")[:50000]

    if suffix == ".json":
        try:
            content = json.loads(report_path.read_text(encoding="utf-8", errors="ignore"))
            return json.dumps(content, ensure_ascii=False)[:50000]
        except Exception:
            return report_path.read_text(encoding="utf-8", errors="ignore")[:50000]

    return report_path.read_text(encoding="utf-8", errors="ignore")[:50000]


def _discover_report_files(report_folder: str) -> list[Path]:
    root = Path(report_folder)
    report_files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_REPORT_SUFFIXES
    ]
    return sorted(report_files)


class FactorReportLoop(FactorRDLoop, metaclass=LoopMeta):
    def __init__(self, report_folder: str = None):
        super().__init__(PROP_SETTING=FACTOR_FROM_REPORT_PROP_SETTING)
        if report_folder is None:
            self.judge_pdf_data_items = json.load(
                open(FACTOR_FROM_REPORT_PROP_SETTING.report_result_json_file_path, "r")
            )
        else:
            self.judge_pdf_data_items = _discover_report_files(report_folder)

        self.loop_n = min(len(self.judge_pdf_data_items), FACTOR_FROM_REPORT_PROP_SETTING.report_limit)
        self.shift_report = (
            0  # some reports does not contain viable factor, so we ship some of them to avoid infinite loop
        )

    async def direct_exp_gen(self, prev_out: dict[str, Any]):
        while True:
            if self.get_unfinished_loop_cnt(self.loop_idx) < RD_AGENT_SETTINGS.get_max_parallel():
                report_file_path = self.judge_pdf_data_items[self.loop_idx + self.shift_report]
                logger.info(f"Processing number {self.loop_idx} report: {report_file_path}")
                exp = extract_hypothesis_and_exp_from_reports(str(report_file_path))
                if exp is None:
                    self.shift_report += 1
                    self.loop_n -= 1
                    if self.loop_n < 0:  # NOTE: on every step, we self.loop_n -= 1 at first.
                        raise self.LoopTerminationError("Reach stop criterion and stop loop")
                    continue
                exp.based_experiments = [QlibFactorExperiment(sub_tasks=[], hypothesis=exp.hypothesis)] + [
                    t[0] for t in self.trace.hist if t[1]
                ]
                exp.sub_workspace_list = exp.sub_workspace_list[: FACTOR_FROM_REPORT_PROP_SETTING.max_factors_per_exp]
                exp.sub_tasks = exp.sub_tasks[: FACTOR_FROM_REPORT_PROP_SETTING.max_factors_per_exp]
                exp.base_features = self.plan["features"]
                if exp.based_experiments:
                    exp.based_experiments[-1].base_features = self.plan["features"]
                logger.log_object(exp.hypothesis, tag="hypothesis generation")
                logger.log_object(exp.sub_tasks, tag="experiment generation")
                return exp
            await asyncio.sleep(1)

    def coding(self, prev_out: dict[str, Any]):
        exp = self.coder.develop(prev_out["direct_exp_gen"])
        logger.log_object(exp.sub_workspace_list, tag="coder result")
        return exp


def main(report_folder=None, path=None, all_duration=None, checkout=True):
    """
    Auto R&D Evolving loop for fintech factors (the factors are extracted from finance reports).

    Args:
        report_folder (str, optional): The folder contains the report PDF files. Reports will be loaded from this folder.
        path (str, optional): The path for loading a session. If provided, the session will be loaded.
        step_n (int, optional): Step number to continue running a session.
    """
    if path is None and report_folder is None:
        model_loop = FactorReportLoop()
    elif path is not None and Path(path).exists():
        model_loop = FactorReportLoop.load(path, checkout=checkout)
    elif path is not None and report_folder is not None:
        logger.warning(f"Session path {path} does not exist. Starting a new run from report_folder instead.")
        model_loop = FactorReportLoop(report_folder=report_folder)
    else:
        model_loop = FactorReportLoop(report_folder=report_folder)

    asyncio.run(model_loop.run(all_duration=all_duration))


if __name__ == "__main__":
    fire.Fire(main)
