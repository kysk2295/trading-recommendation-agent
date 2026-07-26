import ky, { HTTPError, TimeoutError } from "ky";
import {
  type AgentId,
  type Interaction,
  interactionReceiptSchema,
  operatorMessageSchema,
  operatorSessionSchema,
} from "./schema";

type ConnectionState = "connected" | "disconnected";

interface OperatorCallbacks {
  readonly onSession: (authenticated: boolean) => void;
  readonly onConnection: (state: ConnectionState) => void;
  readonly onInteraction: (interaction: Interaction) => void;
}

export class OperatorClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private stopped = false;
  private authenticated = false;

  constructor(private readonly callbacks: OperatorCallbacks) {}

  async start(): Promise<void> {
    this.stopped = false;
    try {
      const session = operatorSessionSchema.parse(
        await ky.get("/api/operator/session", { retry: 0, timeout: 8_000 }).json<unknown>(),
      );
      this.authenticated = session.authenticated;
    } catch (error: unknown) {
      if (!isExpectedNetworkError(error)) {
        throw error;
      }
      this.authenticated = false;
    }
    this.callbacks.onSession(this.authenticated);
    if (this.authenticated) {
      this.connect();
    }
  }

  async submit(agentId: AgentId, command: string): Promise<Interaction> {
    const payload = await ky
      .post(`/api/agents/${agentId}/interactions`, {
        json: { command },
        retry: 0,
        timeout: 15_000,
      })
      .json<unknown>();
    return interactionReceiptSchema.parse(payload).interaction;
  }

  private connect(): void {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/realtime/operator`);
    this.socket = socket;
    socket.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      this.callbacks.onConnection("connected");
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      const parsed = parseMessage(event.data);
      if (parsed !== null) {
        this.callbacks.onInteraction(parsed.interaction);
      }
    });
    socket.addEventListener("close", () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      this.callbacks.onConnection("disconnected");
      this.scheduleReconnect();
    });
    socket.addEventListener("error", () => socket.close());
  }

  private scheduleReconnect(): void {
    if (this.stopped || !this.authenticated || this.reconnectTimer !== null) {
      return;
    }
    const delay = Math.min(60_000, 1_000 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}

function parseMessage(raw: string): ReturnType<typeof operatorMessageSchema.parse> | null {
  try {
    const parsed = operatorMessageSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch (error: unknown) {
    if (error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

function isExpectedNetworkError(error: unknown): boolean {
  return error instanceof HTTPError || error instanceof TimeoutError || error instanceof TypeError;
}
