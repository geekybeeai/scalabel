import {
  BucketLocationConstraint,
  CreateBucketCommand,
  DeleteBucketCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  HeadBucketCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  ListObjectsV2CommandOutput,
  PutObjectCommand,
  S3Client,
  waitUntilObjectNotExists
} from "@aws-sdk/client-s3"
import { fromEnv, fromIni, fromProcess } from "@aws-sdk/credential-providers"
import { AwsCredentialIdentity, Provider } from "@aws-sdk/types"
import _ from "lodash"
import * as path from "path"

import Logger from "./logger"
import { Storage } from "./storage"

/**
 * Build a credential provider that only consults local sources
 * (environment, shared ini files, credential process) so that requests do not
 * hang on EC2/ECS metadata lookups when credentials are missing.
 * Mirrors the explicit provider chain used with SDK v2.
 */
function localCredentialChain(): Provider<AwsCredentialIdentity> {
  const providers: Array<Provider<AwsCredentialIdentity>> = [
    fromEnv(),
    fromIni(),
    fromProcess()
  ]
  return async () => {
    let lastError: unknown = new Error("No AWS credentials found")
    for (const provider of providers) {
      try {
        return await provider()
      } catch (error) {
        lastError = error
      }
    }
    throw lastError
  }
}

/**
 * Implements local file storage
 */
export class S3Storage extends Storage {
  /** the region name */
  protected region: string
  /** the bucket name */
  protected bucketName: string
  /** the aws s3 client */
  protected s3: S3Client

  /**
   * Constructor
   *
   * @param dataPath
   */
  constructor(dataPath: string) {
    // Check s3 data path
    const errorMsg = `s3 data path format is incorrect:
       Got:       ${dataPath}
       Should be: region:bucket/path`
    const error = Error(errorMsg)
    const info = dataPath.split(":")
    if (info.length < 2) {
      throw error
    }
    const bucketPath = info[1].split("/")
    if (bucketPath.length < 2) {
      throw error
    }
    const dataDir = path.join(...bucketPath.splice(1), "/")
    super(dataDir)

    this.region = info[0]
    this.bucketName = bucketPath[0]

    this.s3 = new S3Client({
      region: this.region,
      credentials: localCredentialChain(),
      requestHandler: { connectionTimeout: 10000 },
      maxAttempts: 6
    })
  }

  /**
   * Init bucket
   */
  public async makeBucket(): Promise<void> {
    // Create new bucket if there isn't one already (wait until it exists)
    const hasBucket = await this.hasBucket()
    if (!hasBucket) {
      Logger.info(`Creating Bucket ${this.bucketName}`)
      try {
        await this.s3.send(
          new CreateBucketCommand({
            Bucket: this.bucketName,
            CreateBucketConfiguration: {
              // us-east-1 must not be passed as a location constraint
              LocationConstraint:
                this.region === "us-east-1"
                  ? undefined
                  : (this.region as BucketLocationConstraint)
            }
          })
        )
      } catch (error) {
        Logger.error(error as Error)
      }
    }
  }

  /**
   * Remove the bucket
   */
  public async removeBucket(): Promise<void> {
    Logger.info(`Deleting Bucket ${this.bucketName}`)
    await this.s3.send(new DeleteBucketCommand({ Bucket: this.bucketName }))
  }

  /**
   * Check if specified file exists
   *
   * @param {string} key: relative path of file
   * @param key
   */
  public async hasKey(key: string): Promise<boolean> {
    try {
      await this.s3.send(
        new HeadObjectCommand({
          Bucket: this.bucketName,
          Key: this.fullFile(key)
        })
      )
      return true
    } catch (_error) {
      return false
    }
  }

  /**
   * Lists keys of files at a directory specified by prefix
   * Split by files and directories
   *
   * @param {string} prefix: relative path of directory
   * @param prefix
   */
  public async listKeysOrganized(
    prefix: string
  ): Promise<[string[], string[]]> {
    const fullPrefix = this.fullDir(prefix)
    let continuationToken: string | undefined

    let dirKeys = []
    let fileKeys = []
    for (;;) {
      const data: ListObjectsV2CommandOutput = await this.s3.send(
        new ListObjectsV2Command({
          Bucket: this.bucketName,
          Prefix: fullPrefix,
          ContinuationToken: continuationToken
        })
      )

      if (data.Contents !== undefined) {
        for (const key of data.Contents) {
          // Remove any file extension and prepend prefix
          if (key.Key !== undefined) {
            const noPrefix = key.Key.substr(fullPrefix.length)

            // Parse to get the top level dir or file after prefix
            const parsed = path.parse(noPrefix)
            let keyName = parsed.name
            let isDir = false
            if (parsed.dir.length > 0 && parsed.dir !== "/") {
              const split = parsed.dir.split("/")
              keyName = split[0]
              if (keyName === "") {
                // This handles the case of an extra leading slash: '/dirname'
                keyName = split[1]
              }
              isDir = true
            }
            const finalKey = path.join(prefix, keyName)
            if (isDir) {
              dirKeys.push(finalKey)
            } else {
              fileKeys.push(finalKey)
            }
          }
        }
      }

      if (data.IsTruncated === undefined || !data.IsTruncated) {
        break
      }

      continuationToken = data.NextContinuationToken
    }

    dirKeys = _.uniq(dirKeys)
    fileKeys = _.uniq(fileKeys)
    dirKeys.sort()
    fileKeys.sort()
    return [dirKeys, fileKeys]
  }

  /**
   * Lists keys of files at directory specified by prefix
   *
   * @param {string} prefix: relative path of directory
   * @param {boolean} onlyDir: whether to only return keys that are directories
   * @param prefix
   * @param onlyDir
   */
  public async listKeys(
    prefix: string,
    onlyDir: boolean = false
  ): Promise<string[]> {
    const [dirKeys, fileKeys] = await this.listKeysOrganized(prefix)
    if (onlyDir) {
      return dirKeys
    } else {
      const mergeKeys = dirKeys.concat(fileKeys)
      mergeKeys.sort()
      return mergeKeys
    }
  }

  /**
   * Saves json to at location specified by key
   *
   * @param {string} key: relative path of file
   * @param {string} json: data to save
   * @param key
   * @param json
   */
  public async save(key: string, json: string): Promise<void> {
    await this.s3.send(
      new PutObjectCommand({
        Body: json,
        Bucket: this.bucketName,
        Key: this.fullFile(key)
      })
    )
  }

  /**
   * Loads fields stored at a key
   *
   * @param {string} key: relative path of file
   * @param key
   */
  public async load(key: string): Promise<string> {
    const fullKey = this.fullFile(key)

    if (!(await this.hasKey(key))) {
      throw new Error(`Key '${fullKey}' does not exist`)
    }
    const data = await this.s3.send(
      new GetObjectCommand({ Bucket: this.bucketName, Key: fullKey })
    )
    if (data.Body === undefined) {
      throw new Error(`No data at key '${fullKey}'`)
    } else {
      return await data.Body.transformToString()
    }
  }

  /**
   * Deletes values at the key
   *
   * @param {string} key: relative path of directory
   * @param key
   */
  public async delete(key: string): Promise<void> {
    const [dirKeys, fileKeys] = await this.listKeysOrganized(key)

    const promises = []

    // Delete files
    for (const subKey of fileKeys) {
      const params = {
        Bucket: this.bucketName,
        Key: this.fullFile(subKey)
      }
      promises.push(
        this.s3.send(new DeleteObjectCommand(params)).then(async () => {
          await waitUntilObjectNotExists(
            { client: this.s3, maxWaitTime: 100 },
            params
          )
        })
      )
    }

    // Recursively delete subdirectories
    for (const subKey of dirKeys) {
      promises.push(this.delete(subKey))
    }

    await Promise.all(promises)
  }

  /**
   * make an empty folder object on s3
   *
   * @param key
   */
  public async mkdir(key: string): Promise<void> {
    await this.s3.send(
      new PutObjectCommand({
        Bucket: this.bucketName,
        Key: this.fullDir(key) + "/"
      })
    )
  }

  /**
   * Checks if bucket exists
   */
  private async hasBucket(): Promise<boolean> {
    try {
      await this.s3.send(new HeadBucketCommand({ Bucket: this.bucketName }))
      return true
    } catch (error) {
      return false
    }
  }
}
