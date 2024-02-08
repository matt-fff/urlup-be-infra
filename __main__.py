import json

import pulumi
import pulumi_aws as aws
import pulumi_aws_apigateway as apigateway


def api_usage_plan(
    conf: pulumi.Config,
    api_gateway: apigateway.RestAPI,
) -> aws.apigateway.UsagePlanKey:
    key = aws.apigateway.ApiKey("defaultKey")
    usage = conf.get_object("usage")

    plan = aws.apigateway.UsagePlan(
        "defaultPlan",
        aws.apigateway.UsagePlanArgs(
            api_stages=[
                aws.apigateway.UsagePlanApiStageArgs(
                    api_id=api_gateway.api.id,
                    stage=api_gateway.stage.stage_name,
                ),
            ],
            quota_settings=aws.apigateway.UsagePlanQuotaSettingsArgs(
                limit=int(usage.get("period_limit", 3000)),
                period=usage.get("period_type", "DAY"),
            ),
            throttle_settings=aws.apigateway.UsagePlanThrottleSettingsArgs(
                burst_limit=int(usage.get("burst_limit", 300)),
                rate_limit=int(usage.get("rate_limit", 60)),
            ),
        ),
    )

    plan_key = aws.apigateway.UsagePlanKey(
        "defaultPlanKey",
        aws.apigateway.UsagePlanKeyArgs(
            key_id=key.id,
            key_type="API_KEY",
            usage_plan_id=plan.id,
        ),
    )

    return plan_key


def lambda_role(conf: pulumi.Config, dynamo_table: aws.dynamodb.Table) -> aws.iam.Role:
    tags = conf.get_object("tags")

    dynamo_policy_doc = dynamo_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                        ],
                        "Resource": arn,
                    }
                ],
            }
        )
    )

    assume_role_policy_doc = aws.iam.get_policy_document(
        statements=[
            aws.iam.GetPolicyDocumentStatementArgs(
                effect="Allow",
                principals=[
                    aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                        type="Service",
                        identifiers=["lambda.amazonaws.com"],
                    )
                ],
                actions=["sts:AssumeRole"],
            )
        ]
    )

    # Create an IAM role that the Lambda function can assume
    lambda_role = aws.iam.Role(
        "lambdaRole",
        assume_role_policy=assume_role_policy_doc.json,
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name="dynamoTablePolicy", policy=dynamo_policy_doc
            )
        ],
        managed_policy_arns=[aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE],
        tags=tags,
    )

    return lambda_role


def lambdas(
    conf: pulumi.Config, dynamo_table: aws.dynamodb.Table
) -> dict[str, aws.lambda_.Function]:
    lambdas_dir = conf.get("lambdas_dir") or "../backend/src/urlup_be/lambdas"
    tags = conf.get_object("tags")

    dependencies_layer = aws.lambda_.LayerVersion(
        "lambdaDependenciesLayer",
        layer_name="lambda-dependencies",
        code=pulumi.asset.AssetArchive(
            {".": pulumi.FileArchive(f"{lambdas_dir}/.venv")}
        ),
        compatible_runtimes=["python3.11"],
    )

    lambda_kwargs = dict(
        role=lambda_role(conf, dynamo_table).arn,
        runtime="python3.11",
        layers=[dependencies_layer.arn],
        code=pulumi.asset.AssetArchive(
            {".": pulumi.asset.FileArchive(f"{lambdas_dir}/handlers")}
        ),
        tags=tags,
        environment={
            "variables": {
                "DDB_TABLE": dynamo_table.name,
                "ALLOWED_FRONTENDS": "<<URL_DELIM>>".join(
                    conf.require_object("allowed_frontends")
                ),
                "SENTRY_DSN": conf.get_secret("sentry_dsn"),
            }
        },
    )

    # Create the Lambda functions
    create_lambda = aws.lambda_.Function(
        "shortenLambda", handler="package.shorten.handler", **lambda_kwargs
    )
    redir_lambda = aws.lambda_.Function(
        "redirectLambda", handler="package.redirect.handler", **lambda_kwargs
    )
    get_lambda = aws.lambda_.Function(
        "getLambda", handler="package.get.handler", **lambda_kwargs
    )
    options_lambda = aws.lambda_.Function(
        "optionsLambda", handler="package.options.handler", **lambda_kwargs
    )

    return {
        "create": create_lambda,
        "redirect": redir_lambda,
        "get": get_lambda,
        "options": options_lambda,
    }


def configure_gateway(
    conf: pulumi.Config,
    prefix: str,
    gateway: apigateway.RestAPI,
):
    domain_conf = conf.require_object(f"{prefix}_domain")
    zone_domain = domain_conf["zone_domain"]
    cert_domain = domain_conf["cert_domain"]
    gateway_domain = domain_conf["gateway_domain"]

    zone = aws.route53.get_zone(name=zone_domain)

    cert = aws.acm.get_certificate(
        domain=cert_domain,
        most_recent=True,
        statuses=["ISSUED"],
        opts=pulumi.InvokeOptions(provider=us_east_1),
    )

    # Setup the custom domain name for API Gateway
    gateway_domain_name = aws.apigateway.DomainName(
        f"{prefix}DomainName",
        domain_name=gateway_domain,
        certificate_arn=cert.arn,
    )

    # Create a DNS record to point the custom domain to the API Gateway
    aws.route53.Record(
        f"{prefix}DnsRecord",
        name=gateway_domain,
        type="A",
        zone_id=zone.zone_id,
        aliases=[
            aws.route53.RecordAliasArgs(
                name=gateway_domain_name.cloudfront_domain_name,
                zone_id=gateway_domain_name.cloudfront_zone_id,
                evaluate_target_health=True,
            )
        ],
    )

    base_path_mapping = aws.apigateway.BasePathMapping(
        f"{prefix}PathMapping",
        aws.apigateway.BasePathMappingArgs(
            rest_api=gateway.api,
            domain_name=gateway_domain_name.domain_name,
            stage_name=gateway.stage.stage_name,
        ),
    )

    # Export the URL of the API Gateway
    # to be used to trigger the Lambda function
    pulumi.export(
        f"{prefix}_url",
        pulumi.Output.concat("https://", base_path_mapping.domain_name),
    )


def stack(conf: pulumi.Config):
    # Define the DynamoDB table
    dynamo_table = aws.dynamodb.Table(
        conf.require("table_name"),
        attributes=[
            aws.dynamodb.TableAttributeArgs(
                name="short",
                type="S",
            ),
        ],
        hash_key="short",
        billing_mode="PAY_PER_REQUEST",
        tags=conf.require_object("tags"),
    )

    handlers = lambdas(conf, dynamo_table)

    # Create an API Gateway to trigger the Lambda functions
    api_gateway = apigateway.RestAPI(
        "api",
        stage_name=conf.require("env"),
        request_validator=apigateway.RequestValidator.ALL,
        routes=[
            apigateway.RouteArgs(
                path="/create",
                method=apigateway.Method.POST,
                event_handler=handlers["create"],
                api_key_required=True,
            ),
            apigateway.RouteArgs(
                path="/get",
                method=apigateway.Method.POST,
                event_handler=handlers["get"],
                api_key_required=True,
            ),
            apigateway.RouteArgs(
                path="/create",
                method=apigateway.Method.OPTIONS,
                event_handler=handlers["options"],
                api_key_required=False,
            ),
            apigateway.RouteArgs(
                path="/get",
                method=apigateway.Method.OPTIONS,
                event_handler=handlers["options"],
                api_key_required=False,
            ),
        ],
    )
    plan_key = api_usage_plan(conf, api_gateway)
    pulumi.export("api_key", pulumi.Output.secret(plan_key.value))

    redirect_gateway = apigateway.RestAPI(
        "redirect",
        stage_name=conf.require("env"),
        request_validator=apigateway.RequestValidator.ALL,
        routes=[
            apigateway.RouteArgs(
                path="/{shortcode}",
                method=apigateway.Method.GET,
                event_handler=handlers["redirect"],
                api_key_required=False,
            ),
        ],
    )

    configure_gateway(conf, "api", api_gateway)
    configure_gateway(conf, "redirect", redirect_gateway)


us_east_1 = aws.Provider(
    "us-east-1",
    aws.ProviderArgs(
        region="us-east-1",
    ),
)
config = pulumi.Config()
stack(config)
