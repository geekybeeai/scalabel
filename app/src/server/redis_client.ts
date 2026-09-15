import { createClient } from "redis"

import { RedisConfig } from "../types/config"
import Logger from "./logger"

/** The node-redis client type */
type Client = ReturnType<typeof createClient>
/** The node-redis multi (transaction) type */
export type RedisMulti = ReturnType<Client["multi"]>
/** Message handler signature: (channel, message) */
type MessageHandler = (channel: string, value: string) => void

/**
 * Exposes promisified versions of the necessary methods on a redis client
 * This should implement KeyValue and PubSub interfaces
 */
export class RedisClient {
  /** The redis client for standard key value ops */
  protected client: Client
  /** The redis client for pub/sub of events */
  protected pubSub: Client
  /** Resolves once both clients have attempted to connect */
  protected ready: Promise<void>
  /** Handlers registered via on("message") */
  protected messageHandlers: MessageHandler[]
  /** Handlers registered via on("subscribe") */
  protected subscribeHandlers: Array<() => void>

  /**
   * Constructor
   *
   * @param config
   * @param withLogging
   */
  constructor(config: RedisConfig, withLogging = false) {
    const options = { socket: { host: "127.0.0.1", port: config.port } }
    this.client = createClient(options)
    this.pubSub = createClient(options)
    this.messageHandlers = []
    this.subscribeHandlers = []

    const onError = (err: Error): void => {
      if (withLogging) {
        Logger.error(err)
      }
    }
    this.client.on("error", onError)
    this.pubSub.on("error", onError)

    // Connection failures are reported through the error handlers above;
    // callers keep the same fire-and-forget semantics as before.
    this.ready = Promise.all([
      this.client.connect().catch(onError),
      this.pubSub.connect().catch(onError)
    ]).then(() => undefined)
  }

  /**
   * Add a handler function
   * Note that the handler and subscriber must use the same client
   *
   * @param event
   * @param callback
   */
  public on(event: string, callback: MessageHandler): void {
    if (event === "message") {
      this.messageHandlers.push(callback)
    } else if (event === "subscribe") {
      this.subscribeHandlers.push(callback as unknown as () => void)
    } else {
      this.pubSub.on(event, callback)
    }
  }

  /**
   * Subscribe to a channel
   *
   * @param channel
   */
  public subscribe(channel: string): void {
    void this.ready
      .then(async () => {
        await this.pubSub.subscribe(channel, (message: string, ch: string) => {
          for (const handler of this.messageHandlers) {
            handler(ch, message)
          }
        })
        for (const handler of this.subscribeHandlers) {
          handler()
        }
      })
      .catch((err: Error) => Logger.error(err))
  }

  /**
   * Publish to a channel
   *
   * @param channel
   * @param message
   */
  public publish(channel: string, message: string): void {
    void this.ready
      .then(async () => await this.client.publish(channel, message))
      .catch((err: Error) => Logger.error(err))
  }

  /**
   * Wrapper for redis delete
   *
   * @param key
   */
  public async del(key: string): Promise<void> {
    await this.ready
    await this.client.del(key)
  }

  /** Start an atomic transaction */
  public multi(): RedisMulti {
    return this.client.multi()
  }

  /**
   * Wrapper for redis get
   *
   * @param key
   */
  public async get(key: string): Promise<string | null> {
    await this.ready
    return await this.client.get(key)
  }

  /**
   * Wrapper for redis exists
   *
   * @param key
   */
  public async exists(key: string): Promise<boolean> {
    await this.ready
    return (await this.client.exists(key)) !== 0
  }

  /**
   * Wrapper for redis set add
   *
   * @param key
   * @param value
   */
  public async setAdd(key: string, value: string): Promise<void> {
    await this.ready
    await this.client.sAdd(key, value)
  }

  /**
   * Wrapper for redis set remove
   *
   * @param key
   * @param value
   */
  public async setRemove(key: string, value: string): Promise<void> {
    await this.ready
    await this.client.sRem(key, value)
  }

  /**
   * Wrapper for redis set members
   *
   * @param key
   */
  public async getSetMembers(key: string): Promise<string[]> {
    await this.ready
    return await this.client.sMembers(key)
  }

  /**
   * Wrapper for redis psetex
   *
   * @param key
   * @param timeout
   * @param value
   */
  public async psetex(
    key: string,
    timeout: number,
    value: string
  ): Promise<void> {
    await this.ready
    await this.client.pSetEx(key, timeout, value)
  }

  /**
   * Wrapper for redis set
   *
   * @param key
   * @param value
   */
  public async set(key: string, value: string): Promise<void> {
    await this.ready
    await this.client.set(key, value)
  }

  /**
   * This function is used to get keys with a given prefix.
   * This is useful when deleting project.
   *
   * @param prefix
   */
  public async getKeysWithPrefix(prefix: string): Promise<string[]> {
    await this.ready
    return await this.client.keys(prefix + "*")
  }

  /**
   * Wrapper for redis config
   *
   * @param type
   * @param name
   * @param value
   */
  public config(type: string, name: string, value: string): void {
    void this.ready
      .then(async () => {
        if (type.toUpperCase() === "SET") {
          await this.client.configSet(name, value)
        }
      })
      .catch((err: Error) => Logger.error(err))
  }

  /** Close the connection to the server */
  public async close(): Promise<void> {
    // Do not wait for `ready`: against an unreachable server the connect
    // promise never settles, so tear the sockets down directly instead.
    for (const c of [this.client, this.pubSub]) {
      if (c.isOpen) {
        await c.close()
      } else {
        c.destroy()
      }
    }
  }
}
