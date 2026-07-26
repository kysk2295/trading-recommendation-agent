import { requiredElement, textElement } from "./dom";
import { shortTime, stateLabel, statusClass } from "./format";
import { OperatorClient } from "./operator_client";
import type { AgentId, AgentView, Interaction } from "./schema";

const agentIds = [
  "kr-theme",
  "us-intraday",
  "us-systematic",
  "us-swing",
  "research",
  "delivery",
] as const satisfies readonly AgentId[];

const agentLabels: Readonly<Record<AgentId, readonly [string, string]>> = {
  "kr-theme": ["한국 테마", "KR THEME / INTRADAY"],
  "us-intraday": ["미국 장중", "US INTRADAY"],
  "us-systematic": ["미국 시스템", "US SYSTEMATIC"],
  "us-swing": ["미국 스윙", "US SWING"],
  research: ["데이터 연구", "CAUSAL RESEARCH"],
  delivery: ["추천 전달", "DELIVERY & OPERATIONS"],
};

export class AgentWorkspace {
  private readonly selector = requiredElement("agent-selector", HTMLElement);
  private readonly form = requiredElement("interaction-form", HTMLFormElement);
  private readonly textarea = requiredElement("interaction-command", HTMLTextAreaElement);
  private readonly submit = requiredElement("interaction-submit", HTMLButtonElement);
  private readonly status = requiredElement("operator-status", HTMLElement);
  private readonly guidance = requiredElement("interaction-guidance", HTMLElement);
  private readonly interactions = new Map<string, Interaction>();
  private readonly agents = new Map<AgentId, AgentView>();
  private readonly operator: OperatorClient;
  private selected: AgentId = "kr-theme";
  private authenticated = false;
  private connected = false;
  private submitting = false;

  constructor() {
    this.operator = new OperatorClient({
      onSession: (authenticated) => {
        this.authenticated = authenticated;
        this.updateCommandState();
      },
      onConnection: (state) => {
        this.connected = state === "connected";
        this.updateCommandState();
      },
      onInteraction: (interaction) => this.applyInteraction(interaction),
    });
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.submitCommand();
    });
    this.textarea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.ctrlKey) {
        event.preventDefault();
        this.form.requestSubmit();
      }
    });
    this.renderSelector();
    this.renderSelectedAgent();
    this.updateCommandState();
  }

  start(): void {
    void this.operator.start();
  }

  updateAgents(agents: readonly AgentView[]): void {
    this.agents.clear();
    for (const agent of agents) {
      this.agents.set(agent.agent_id, agent);
    }
    this.renderSelector();
    this.renderSelectedAgent();
  }

  private select(agentId: AgentId): void {
    this.selected = agentId;
    this.renderSelector();
    this.renderSelectedAgent();
    this.renderInteractions();
    this.textarea.focus();
  }

  private renderSelector(): void {
    this.selector.replaceChildren(
      ...agentIds.map((agentId) => {
        const [label, scope] = agentLabels[agentId];
        const runtime = this.agents.get(agentId);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "agent-choice";
        button.dataset["agentId"] = agentId;
        button.setAttribute("aria-pressed", String(agentId === this.selected));
        button.addEventListener("click", () => this.select(agentId));
        const identity = document.createElement("span");
        identity.append(textElement("strong", label), textElement("small", scope));
        button.append(
          identity,
          textElement(
            "span",
            stateLabel(runtime?.state ?? "idle"),
            `state-text ${statusClass(runtime?.state ?? "idle")}`,
          ),
        );
        return button;
      }),
    );
  }

  private renderSelectedAgent(): void {
    const [label, scope] = agentLabels[this.selected];
    const runtime = this.agents.get(this.selected);
    requiredElement("selected-agent-title", HTMLElement).textContent = label;
    requiredElement("selected-agent-scope", HTMLElement).textContent = scope;
    const state = requiredElement("selected-agent-state", HTMLElement);
    state.textContent = stateLabel(runtime?.state ?? "idle");
    state.className = `status-word ${statusClass(runtime?.state ?? "idle")}`;
    requiredElement("selected-agent-job", HTMLElement).textContent =
      runtime?.scheduled_label ?? "현재 등록된 실행·예약 작업 없음";
  }

  private updateCommandState(): void {
    const ready = this.authenticated && this.connected && !this.submitting;
    this.textarea.disabled = !ready;
    this.submit.disabled = !ready;
    if (!this.authenticated) {
      this.status.textContent = "조회 전용 · 기기 페어링 필요";
      this.status.className = "operator-state state-locked";
      this.guidance.textContent =
        "Mac publisher가 발급한 일회용 링크로 연결하면 명령 입력이 활성화됩니다.";
      return;
    }
    if (!this.connected) {
      this.status.textContent = "명령 채널 재연결 중";
      this.status.className = "operator-state state-armed";
      this.guidance.textContent = "연결이 복구되면 입력이 자동으로 활성화됩니다.";
      return;
    }
    this.status.textContent = this.submitting ? "명령 접수 중" : "명령 접수 채널 연결됨";
    this.status.className = "operator-state state-ready";
    this.guidance.textContent =
      "Ctrl+Enter로 전송 · 명령 1건당 Hermes 1회 실행 · 자동 유료 재시도 없음";
  }

  private async submitCommand(): Promise<void> {
    const command = this.textarea.value.trim();
    if (!this.authenticated || !this.connected || this.submitting || command.length === 0) {
      return;
    }
    this.submitting = true;
    this.updateCommandState();
    try {
      const interaction = await this.operator.submit(this.selected, command);
      this.applyInteraction(interaction);
      this.textarea.value = "";
    } catch (_error: unknown) {
      this.guidance.textContent =
        "명령을 접수하지 못했습니다. 연결 상태를 확인한 뒤 다시 보내세요.";
    } finally {
      this.submitting = false;
      this.updateCommandState();
    }
  }

  private applyInteraction(interaction: Interaction): void {
    this.interactions.set(
      interaction.id,
      latestInteraction(this.interactions.get(interaction.id), interaction),
    );
    if (interaction.agent_id === this.selected) {
      this.renderInteractions();
    }
  }

  private renderInteractions(): void {
    const stream = requiredElement("interaction-stream", HTMLElement);
    const interactions = [...this.interactions.values()]
      .filter((interaction) => interaction.agent_id === this.selected)
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
    if (interactions.length === 0) {
      stream.replaceChildren(
        textElement("p", "아직 이 에이전트에게 보낸 명령이 없습니다.", "empty-state"),
      );
      return;
    }
    stream.replaceChildren(...interactions.map(renderInteraction));
  }
}

export function latestInteraction(
  current: Interaction | undefined,
  incoming: Interaction,
): Interaction {
  return current !== undefined && current.updated_at > incoming.updated_at ? current : incoming;
}

function renderInteraction(interaction: Interaction): HTMLElement {
  const article = document.createElement("article");
  article.className = `interaction-item interaction-${interaction.state}`;
  const heading = document.createElement("div");
  const time = document.createElement("time");
  time.dateTime = interaction.updated_at;
  time.textContent = shortTime(interaction.updated_at);
  heading.append(
    textElement("span", stateLabel(interaction.state), statusClass(interaction.state)),
    time,
  );
  article.append(heading, textElement("p", interaction.command, "interaction-command"));
  if (interaction.response !== null) {
    article.append(textElement("p", interaction.response, "interaction-response"));
  } else {
    article.append(
      textElement(
        "p",
        interaction.state === "running"
          ? "Hermes가 목표를 처리하고 있습니다."
          : "실행 순서를 기다립니다.",
        "interaction-pending",
      ),
    );
  }
  return article;
}
