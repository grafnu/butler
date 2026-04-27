

graph TD
    %% External Dependencies
    subgraph External_Context [External Context]
        Manufacturer[Manufacturer] -- "Firmware Blobs" --> Ingestion
        SiteModel[(Site Model Database)] -- "Property Changes (Trigger)" --> Butler
        UDMIS[UDMIS / Schema Libraries] -.-> Butler
    end

    %% Internal Butler System
    subgraph Butler_System [Butler Orchestration Engine]
        Ingestion[Ingestion Interface / CLI] --> BlobRepo
        BlobRepo[(Blob Repository / Object Store)] --> Multiplexer
        Butler[Core Logic / Monitor] --> Multiplexer
        Multiplexer[Configuration Multiplexing] --> Payload[Payload Generation]
    end

    %% Delivery & Feedback
    subgraph Execution_Loop [Execution & Feedback]
        Payload --> Transport{MQTT / HTTP}
        Transport --> Devices[[Device Fleet]]
        Devices -- "Status / State Updates" --> Butler
        Butler -- "Failures / Errors" --> Alerts[Alerting / User Feedback]
    end

    %% Styling
    style Butler_System fill:#f9f,stroke:#333,stroke-width:2px
    style External_Context fill:#dfd,stroke:#333
    style Execution_Loop fill:#ddf,stroke:#333
