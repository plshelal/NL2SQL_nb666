FROM elasticsearch:8.11.0

# 安装 IK 中文分词插件(版本必须与 ES 严格匹配)
RUN elasticsearch-plugin install --batch \
    https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.0.zip
