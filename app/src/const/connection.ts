/* endpoint names for python server */
export const enum ModelEndpoint {
  PREDICT_POLY = "predictPoly",
  REFINE_POLY = "refinePoly"
}

/* endpoint names for http server */
export const enum Endpoint {
  POST_PROJECT = "/postProject",
  POST_PROJECT_INTERNAL = "/postProjectInternal",
  GET_PROJECT_NAMES = "/getProjectNames",
  EXPORT = "/getExport",
  TILE_EXPORT = "/getTileData",
  DASHBOARD = "/getDashboardContents",
  GET_TASK_METADATA = "/getTaskMetaData",
  POST_TASKS = "/postTasks",
  CALLBACK = "/callback",
  STATS = "/stats",
  DELETE_PROJECT = "/deleteProject",
  OPEN_EDIT_SESSION = "/openEditSession",
  CLOSE_EDIT_SESSION = "/closeEditSession"
}

/* socket.io event names */
export const enum EventName {
  ACTION_BROADCAST = "actionBroadcast",
  ACTION_SEND = "actionSend",
  REGISTER_ACK = "registerAck",
  REGISTER = "register",
  CONNECTION = "connection",
  CONNECT = "connect",
  DISCONNECT = "disconnect"
}
